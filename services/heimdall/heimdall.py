#!/usr/bin/env python3
"""
Heimdall — Market Regime-Detection via a Gaussian Hidden Markov Model.

Named for the ever-watchful Norse guardian who sees across the nine realms,
Heimdall watches the market's hidden STATE. It consumes the live feature stream
(features.obi.v1) that obi-bridge emits, builds a small standardised observation
vector suited to regime detection, and fits a multivariate Gaussian Hidden
Markov Model with the Baum-Welch (EM) algorithm to infer the latent regime the
market is currently in.

This is a direct homage to Renaissance Technologies / Jim Simons: Leonard Baum,
co-inventor of the Baum-Welch algorithm, worked AT Renaissance, and hidden
Markov models are part of the folklore of how Medallion read hidden market
states. Heimdall implements the mathematics honestly and from first principles
(pure numpy): forward-backward in LOG space for numerical stability,
Baum-Welch/EM training with covariance regularisation, Viterbi decoding, and
exact log-likelihood.

HONEST FRAMING: this is a trading SIMULATION with no measured out-of-sample
edge (the stack's own research gateway reports PBO = 1.00). Heimdall detects and
labels statistical regimes; it makes NO claim that trading those regimes is
profitable. The regime labels ("calm", "trending", "turbulent") are DERIVED from
the fitted emission parameters, never hardcoded, and the derivation is exposed
under /api/regime/model so it is fully transparent. Until there is enough data to
fit a model, /api/regime returns {trained:false} and NEVER fabricates a regime.

OBSERVATION VECTOR (3 well-motivated, standardised features):
  1. trend        — obi-bridge `momentum` (the 5-minute EMA-crossover momentum);
                    a signed directional feature that separates trending from
                    range-bound states.
  2. volatility   — obi-bridge `volatility` (ATR-based realised volatility);
                    the classic driver of a calm-vs-turbulent regime split.
  3. imbalance    — |obi| (absolute order-book imbalance); imbalance INTENSITY
                    (magnitude, direction-agnostic) rises in stressed / one-sided
                    books, complementing volatility.
Each feature is z-scored against the current rolling window before fitting, so
the HMM sees comparably-scaled dimensions and the fitted means/covariances are in
standardised units (documented in /api/regime/model).

No API key required. Runs as a Docker service alongside the Norse Stack.
Modelled on services/forseti/forseti.py and services/mimir/mimir.py: same stdlib
http.server + ThreadingHTTPServer + JSON/CORS helper + lock-guarded tracker +
Kafka consumer pattern + startup logging. numpy is the sole non-stdlib runtime
dependency (an HMM needs linear algebra).
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FEATURES_TOPIC = os.environ.get("FEATURES_TOPIC", "features.obi.v1")
PORT = int(os.environ.get("HEIMDALL_PORT", "8097"))

# Number of latent regimes (states). Three is the natural default for a
# calm / trending / turbulent taxonomy, but is fully configurable.
N_STATES = int(os.environ.get("HEIMDALL_N_STATES", "3"))

# Rolling observation window the HMM is (re)fit over. Bounded so memory and fit
# cost stay flat over a long-lived stream.
WINDOW_SIZE = int(os.environ.get("HEIMDALL_WINDOW", "1000"))

# Minimum observations before the FIRST fit. Below this we report trained:false
# rather than fitting a meaningless model to a handful of points. A sane floor is
# well above n_states so every state can be populated.
MIN_OBS_TO_FIT = int(os.environ.get("HEIMDALL_MIN_OBS", "200"))

# Refit cadence: refit once this many new observations have arrived since the
# last fit (a cheap, deterministic trigger; no background timer needed).
REFIT_EVERY = int(os.environ.get("HEIMDALL_REFIT_EVERY", "50"))

# Warm-start source. A regime model needs training data, but on a freshly
# restarted stack the Kafka topic backlog is empty and the live feature feed is
# slow (~minutes to reach MIN_OBS). Mimir is the point-in-time feature store and
# persists history, so we seed the rolling window from it on startup. Best-effort:
# if Mimir is unreachable or empty the service just warms up from the live stream.
MIMIR_URL = os.environ.get("MIMIR_URL", "http://mimir:8095").rstrip("/")
MIMIR_BACKFILL = os.environ.get("HEIMDALL_MIMIR_BACKFILL", "true").lower() in (
    "1", "true", "yes", "on")
MIMIR_BACKFILL_LIMIT = int(os.environ.get("HEIMDALL_MIMIR_BACKFILL_LIMIT", "400"))

# EM controls.
EM_MAX_ITER = int(os.environ.get("HEIMDALL_EM_MAX_ITER", "100"))
EM_TOL = float(os.environ.get("HEIMDALL_EM_TOL", "1e-4"))
# Number of random restarts per fit; the best final log-likelihood wins. EM only
# guarantees a LOCAL optimum, so several inits materially improve recovery.
EM_N_INIT = int(os.environ.get("HEIMDALL_EM_N_INIT", "5"))
# Covariance floor added to every state covariance each M-step, as a fraction of
# the (standardised) feature variance. Prevents a state collapsing onto a few
# points and producing a singular / exploding covariance.
COVAR_REG = float(os.environ.get("HEIMDALL_COVAR_REG", "1e-3"))

# Recent regime assignments retained for /api/regime/history.
HISTORY_MAX = int(os.environ.get("HEIMDALL_HISTORY_MAX", "5000"))

# CORS: default "*" to match the sibling services; lockable in hardened deploys.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the consumer stamps a heartbeat each poll cycle. /healthz reports
# degraded once the heartbeat is older than this, so a wedged consumer is
# detectable even while the HTTP server stays up.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("heimdall")

shutdown = False

FEATURE_NAMES = ["trend_m5_momentum", "volatility_atr", "abs_obi_imbalance"]


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


def _parse_ts(ts):
    """Parse an ISO-8601 timestamp to an aware datetime, or None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ===========================================================================
# Gaussian Hidden Markov Model (pure numpy).
#
# All of the recursions run in LOG space to stay numerically stable over long
# sequences (products of thousands of small emission likelihoods otherwise
# underflow to zero). The public surface is deliberately small and matches the
# hmmlearn vocabulary so it reads familiarly:
#
#   startprob_ (K,)      initial state distribution      pi
#   transmat_  (K, K)    row-stochastic transition matrix A  (A[i, j] = P(j|i))
#   means_     (K, D)    per-state emission mean          mu_k
#   covars_    (K, D, D) per-state full emission covariance Sigma_k
#
# fit()   — Baum-Welch / EM, returns self and records loglik per iteration.
# score() — exact data log-likelihood under the model (forward pass).
# predict()/decode() — Viterbi most-likely state path.
# filter() — forward-only (causal) last-step state posterior.
# ===========================================================================

_LOG_2PI = float(np.log(2.0 * np.pi))


def _logsumexp(a, axis=None):
    """Numerically-stable log-sum-exp that tolerates -inf entries.

    Rows that are entirely -inf collapse to -inf without emitting a NaN (the
    naive ``max`` would give -inf and ``exp(-inf - -inf)`` a NaN), which matters
    because a forbidden transition legitimately contributes log-prob -inf.
    """
    a = np.asarray(a, dtype=float)
    a_max = np.max(a, axis=axis, keepdims=True)
    # Replace -inf maxima with 0 so the subtraction is well-defined; those rows
    # sum to exp(0)*... = 0 contributions and we restore -inf at the end.
    a_max_safe = np.where(np.isfinite(a_max), a_max, 0.0)
    s = np.sum(np.exp(a - a_max_safe), axis=axis, keepdims=True)
    with np.errstate(divide="ignore"):
        out = np.log(s) + a_max_safe
    out = np.where(np.isfinite(a_max), out, a_max)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class GaussianHMM:
    """Multivariate Gaussian HMM trained by Baum-Welch (pure numpy)."""

    def __init__(self, n_states, n_features, covar_reg=COVAR_REG):
        self.n_states = int(n_states)
        self.n_features = int(n_features)
        self.covar_reg = float(covar_reg)
        self.startprob_ = None
        self.transmat_ = None
        self.means_ = None
        self.covars_ = None
        self.loglik_history_ = []
        self.n_iter_ = 0
        self.converged_ = False

    # -- emission density -----------------------------------------------------

    def _log_emission(self, X):
        """Log N(x_t; mu_k, Sigma_k) for every (t, k). Returns (T, K).

        Uses a Cholesky factorisation per state for a stable log-determinant and
        Mahalanobis term. A state whose covariance is (numerically) not positive
        definite is nudged by an isotropic jitter before factorising.
        """
        X = np.asarray(X, dtype=float)
        T = X.shape[0]
        K = self.n_states
        D = self.n_features
        out = np.empty((T, K), dtype=float)
        for k in range(K):
            cov = self.covars_[k]
            try:
                chol = np.linalg.cholesky(cov)
            except np.linalg.LinAlgError:
                cov = cov + np.eye(D) * (self.covar_reg + 1e-6)
                chol = np.linalg.cholesky(cov)
            diff = (X - self.means_[k]).T                 # (D, T)
            z = np.linalg.solve(chol, diff)               # (D, T), L z = diff
            maha = np.sum(z * z, axis=0)                   # (T,)
            log_det = 2.0 * np.sum(np.log(np.diag(chol)))
            out[:, k] = -0.5 * (D * _LOG_2PI + log_det + maha)
        return out

    # -- forward / backward (log space) --------------------------------------

    def _forward(self, log_emission):
        """Return (log_alpha (T, K), loglik). Standard scaled-free log forward."""
        T, K = log_emission.shape
        log_startprob = np.log(self.startprob_)
        log_transmat = np.log(self.transmat_)
        log_alpha = np.empty((T, K), dtype=float)
        log_alpha[0] = log_startprob + log_emission[0]
        for t in range(1, T):
            # log_alpha[t, j] = logsumexp_i(log_alpha[t-1, i] + logA[i, j]) + b
            log_alpha[t] = (
                _logsumexp(log_alpha[t - 1][:, None] + log_transmat, axis=0)
                + log_emission[t]
            )
        loglik = _logsumexp(log_alpha[T - 1])
        return log_alpha, loglik

    def _backward(self, log_emission):
        """Return log_beta (T, K)."""
        T, K = log_emission.shape
        log_transmat = np.log(self.transmat_)
        log_beta = np.zeros((T, K), dtype=float)  # log 1 = 0 at t = T-1
        for t in range(T - 2, -1, -1):
            # log_beta[t, i] = logsumexp_j(logA[i, j] + b[t+1, j] + beta[t+1, j])
            tmp = log_transmat + (log_emission[t + 1] + log_beta[t + 1])[None, :]
            log_beta[t] = _logsumexp(tmp, axis=1)
        return log_beta

    # -- log-likelihood -------------------------------------------------------

    def score(self, X):
        """Exact log-likelihood of X under the model (forward total)."""
        log_emission = self._log_emission(X)
        _, loglik = self._forward(log_emission)
        return loglik

    # -- Baum-Welch / EM ------------------------------------------------------

    def _init_params(self, X, rng):
        """Random restart initialisation.

        Means seeded from K distinct random observations (k-means++-lite: first
        random, rest chosen to be far from those already picked), covariances
        from the global data covariance, near-uniform start and transition
        distributions with a diagonal bias (regimes persist).
        """
        X = np.asarray(X, dtype=float)
        T, D = X.shape
        K = self.n_states

        # Farthest-point-ish seeding for well-separated initial means.
        idx0 = int(rng.integers(T))
        chosen = [idx0]
        for _ in range(1, K):
            pts = X[np.array(chosen)]
            # distance of every point to the nearest already-chosen mean
            d = np.min(
                np.sum((X[:, None, :] - pts[None, :, :]) ** 2, axis=2), axis=1
            )
            # sample proportional to squared distance (k-means++), guarding the
            # degenerate all-zero case.
            total = d.sum()
            if total <= 0 or not np.isfinite(total):
                nxt = int(rng.integers(T))
            else:
                nxt = int(rng.choice(T, p=d / total))
            chosen.append(nxt)
        means = X[np.array(chosen)].copy()

        global_cov = np.cov(X.T)
        if global_cov.ndim == 0:  # D == 1
            global_cov = np.array([[float(global_cov)]])
        global_cov = global_cov + np.eye(D) * self._reg_floor(X)
        covars = np.array([global_cov.copy() for _ in range(K)])

        startprob = np.full(K, 1.0 / K)
        # Diagonal-biased transition matrix: 0.8 self, remainder spread evenly.
        transmat = np.full((K, K), 0.2 / max(K - 1, 1))
        np.fill_diagonal(transmat, 0.8)
        transmat /= transmat.sum(axis=1, keepdims=True)

        self.startprob_ = startprob
        self.transmat_ = transmat
        self.means_ = means
        self.covars_ = covars

    def _reg_floor(self, X):
        """Absolute covariance floor = covar_reg * mean feature variance."""
        var = np.var(np.asarray(X, dtype=float), axis=0)
        base = float(np.mean(var)) if var.size else 1.0
        if not np.isfinite(base) or base <= 0:
            base = 1.0
        return self.covar_reg * base

    def fit(self, X, max_iter=EM_MAX_ITER, tol=EM_TOL, rng=None):
        """Fit by Baum-Welch/EM from the CURRENT parameters (single restart).

        Records the per-iteration log-likelihood in ``loglik_history_`` (computed
        at the start of each iteration under the parameters going INTO that
        iteration), so a correct EM run yields a non-decreasing sequence.
        """
        X = np.asarray(X, dtype=float)
        T, D = X.shape
        if rng is None:
            rng = np.random.default_rng()
        if self.means_ is None:
            self._init_params(X, rng)

        reg = self._reg_floor(X) * np.eye(D)
        self.loglik_history_ = []
        prev_ll = -np.inf
        self.converged_ = False

        for it in range(max_iter):
            # -------- E-step --------
            log_emission = self._log_emission(X)
            log_alpha, loglik = self._forward(log_emission)
            log_beta = self._backward(log_emission)

            self.loglik_history_.append(float(loglik))

            # gamma[t, k] = P(state_t = k | X): normalise alpha*beta per t.
            log_gamma = log_alpha + log_beta
            log_gamma -= _logsumexp(log_gamma, axis=1)[:, None]
            gamma = np.exp(log_gamma)

            # xi summed over t: expected i->j transition counts.
            log_transmat = np.log(self.transmat_)
            # log_xi[t, i, j] = alpha[t,i]+logA[i,j]+b[t+1,j]+beta[t+1,j]-loglik
            log_xi = (
                log_alpha[:-1, :, None]
                + log_transmat[None, :, :]
                + (log_emission[1:] + log_beta[1:])[:, None, :]
                - loglik
            )
            xi_sum = np.exp(_logsumexp(log_xi, axis=0))   # (K, K)

            # -------- M-step --------
            self.startprob_ = gamma[0] + 1e-12
            self.startprob_ /= self.startprob_.sum()

            trans_den = xi_sum.sum(axis=1, keepdims=True)
            trans_den = np.where(trans_den > 0, trans_den, 1.0)
            self.transmat_ = xi_sum / trans_den
            # Guard any all-zero row (an unvisited state) with a uniform row.
            row_sums = self.transmat_.sum(axis=1)
            for k in range(self.n_states):
                if not np.isfinite(row_sums[k]) or row_sums[k] <= 0:
                    self.transmat_[k] = np.full(self.n_states, 1.0 / self.n_states)

            Nk = gamma.sum(axis=0)                         # (K,)
            Nk_safe = np.where(Nk > 1e-12, Nk, 1e-12)
            means = (gamma.T @ X) / Nk_safe[:, None]       # (K, D)

            covars = np.empty((self.n_states, D, D), dtype=float)
            for k in range(self.n_states):
                diff = X - means[k]                        # (T, D)
                weighted = diff * gamma[:, k][:, None]     # (T, D)
                cov = (weighted.T @ diff) / Nk_safe[k]     # (D, D)
                covars[k] = cov + reg                      # regularise
            # A state that captured essentially no mass keeps its prior mean but
            # takes the regularised global-ish covariance, avoiding NaNs.
            for k in range(self.n_states):
                if Nk[k] <= 1e-8:
                    means[k] = self.means_[k]
            self.means_ = means
            self.covars_ = covars

            # -------- convergence --------
            if it > 0 and (loglik - prev_ll) < tol:
                self.converged_ = True
                self.n_iter_ = it + 1
                break
            prev_ll = loglik
        else:
            self.n_iter_ = max_iter

        return self

    def fit_best(self, X, n_init=EM_N_INIT, max_iter=EM_MAX_ITER, tol=EM_TOL,
                 seed=None):
        """Fit ``n_init`` random restarts; keep the highest final log-likelihood.

        EM only finds a local optimum, so multiple restarts are how we make
        parameter recovery reliable. Returns self, holding the best model.
        """
        X = np.asarray(X, dtype=float)
        base = np.random.default_rng(seed)
        best = None
        best_ll = -np.inf
        best_hist = None
        for i in range(max(1, n_init)):
            rng = np.random.default_rng(base.integers(2**63 - 1))
            cand = GaussianHMM(self.n_states, self.n_features, self.covar_reg)
            cand._init_params(X, rng)
            cand.fit(X, max_iter=max_iter, tol=tol, rng=rng)
            ll = cand.score(X)
            if ll > best_ll or best is None:
                best_ll = ll
                best = cand
                best_hist = cand.loglik_history_
        self.startprob_ = best.startprob_
        self.transmat_ = best.transmat_
        self.means_ = best.means_
        self.covars_ = best.covars_
        self.loglik_history_ = best_hist
        self.n_iter_ = best.n_iter_
        self.converged_ = best.converged_
        return self

    # -- inference ------------------------------------------------------------

    def predict_proba(self, X):
        """Smoothed state posteriors gamma[t, k] (uses full sequence)."""
        log_emission = self._log_emission(X)
        log_alpha, loglik = self._forward(log_emission)
        log_beta = self._backward(log_emission)
        log_gamma = log_alpha + log_beta
        log_gamma -= _logsumexp(log_gamma, axis=1)[:, None]
        return np.exp(log_gamma)

    def filter(self, X):
        """Causal forward-only last-step posterior P(state_T | x_1..T). (K,).

        This is what the LIVE regime should be reported from: it conditions only
        on observations up to now, never peeking at the future (unlike the
        smoothed gamma). Returns the last-step normalised alpha.
        """
        log_emission = self._log_emission(X)
        log_alpha, _ = self._forward(log_emission)
        last = log_alpha[-1]
        last = last - _logsumexp(last)
        return np.exp(last)

    def decode(self, X):
        """Viterbi most-likely state path. Returns (path (T,), logprob)."""
        X = np.asarray(X, dtype=float)
        log_emission = self._log_emission(X)
        T, K = log_emission.shape
        log_startprob = np.log(self.startprob_)
        log_transmat = np.log(self.transmat_)

        delta = np.empty((T, K), dtype=float)
        psi = np.zeros((T, K), dtype=int)
        delta[0] = log_startprob + log_emission[0]
        for t in range(1, T):
            # for each j: best previous i
            scores = delta[t - 1][:, None] + log_transmat   # (K_i, K_j)
            psi[t] = np.argmax(scores, axis=0)
            delta[t] = scores[psi[t], np.arange(K)] + log_emission[t]

        path = np.empty(T, dtype=int)
        path[T - 1] = int(np.argmax(delta[T - 1]))
        logprob = float(delta[T - 1, path[T - 1]])
        for t in range(T - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path, logprob

    # -- sampling (used by the correctness tests) -----------------------------

    def sample(self, n, rng=None):
        """Sample (X (n, D), states (n,)) from the model. For tests / demos."""
        if rng is None:
            rng = np.random.default_rng()
        K, D = self.n_states, self.n_features
        states = np.empty(n, dtype=int)
        X = np.empty((n, D), dtype=float)
        states[0] = int(rng.choice(K, p=self.startprob_))
        for t in range(1, n):
            states[t] = int(rng.choice(K, p=self.transmat_[states[t - 1]]))
        for t in range(n):
            k = states[t]
            X[t] = rng.multivariate_normal(self.means_[k], self.covars_[k])
        return X, states


def stationary_distribution(transmat):
    """Stationary distribution pi solving pi = pi A, pi >= 0, sum pi = 1.

    Computed as the left eigenvector of A for eigenvalue 1 (equivalently the
    eigenvector of A^T for eigenvalue 1). Falls back to the uniform distribution
    if the numerical solve degenerates.
    """
    A = np.asarray(transmat, dtype=float)
    K = A.shape[0]
    try:
        vals, vecs = np.linalg.eig(A.T)
        idx = int(np.argmin(np.abs(vals - 1.0)))
        v = np.real(vecs[:, idx])
        v = np.abs(v)
        s = v.sum()
        if s <= 0 or not np.isfinite(s):
            return np.full(K, 1.0 / K)
        return v / s
    except np.linalg.LinAlgError:
        return np.full(K, 1.0 / K)


def derive_labels(means, covars, feature_names=FEATURE_NAMES):
    """Derive human-readable regime labels from FITTED parameters.

    Deterministic pure function (no randomness, no hidden state): the SAME fitted
    parameters always yield the SAME labels, so the mapping is reproducible and
    auditable. Nothing is hardcoded per state index — the label of a state is a
    function only of its fitted emission mean and covariance.

    Method (transparent, exposed under /api/regime/model):
      * turbulence(k) = fitted mean of the volatility feature
                        + tr(Sigma_k) / D   (total emission dispersion).
        A regime is "turbulent" when its volatility mean AND/OR its emission
        spread is high; "calm" when both are low.
      * trend_strength(k) = |fitted mean of the trend feature|.
      * The lowest-turbulence state is "calm", the highest is "turbulent"; the
        remaining state(s) are "trending" when their trend strength is the
        largest among the middle band, else "choppy".
    Ties break by state index so the result is fully deterministic.
    """
    means = np.asarray(means, dtype=float)
    covars = np.asarray(covars, dtype=float)
    K, D = means.shape

    vol_idx = 1 if D > 1 else 0          # volatility feature position
    trend_idx = 0                        # trend feature position

    disp = np.array([np.trace(covars[k]) / D for k in range(K)])
    turbulence = means[:, vol_idx] + disp
    trend_strength = np.abs(means[:, trend_idx])

    # Rank states by turbulence ascending; ties -> lower index first.
    order = sorted(range(K), key=lambda k: (turbulence[k], k))
    calm_state = order[0]
    turbulent_state = order[-1]
    middle = order[1:-1] if K > 2 else []

    labels = [None] * K
    interp = [None] * K
    labels[calm_state] = "calm"
    if K > 1:
        labels[turbulent_state] = "turbulent"

    # Among the middle band the strongest directional state is "trending".
    if middle:
        trend_leader = max(middle, key=lambda k: (trend_strength[k], -k))
        for k in middle:
            labels[k] = "trending" if k == trend_leader else "choppy"
    elif K == 2:
        # Two-state model: the non-calm state is simply "turbulent" (already set).
        pass

    for k in range(K):
        if labels[k] is None:
            labels[k] = "trending"

    for k in range(K):
        interp[k] = {
            "state": int(k),
            "label": labels[k],
            "turbulenceScore": round(float(turbulence[k]), 6),
            "trendStrength": round(float(trend_strength[k]), 6),
            "volatilityMean": round(float(means[k, vol_idx]), 6),
            "trendMean": round(float(means[k, trend_idx]), 6),
            "emissionDispersion": round(float(disp[k]), 6),
        }
    return labels, interp


# ===========================================================================
# Liveness (mirrors forseti/mimir).
# ===========================================================================

class Liveness:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_beat = None
        self._started = False

    def mark_started(self):
        with self._lock:
            self._started = True
            self._last_beat = time.monotonic()

    def beat(self):
        with self._lock:
            self._last_beat = time.monotonic()

    def status(self):
        with self._lock:
            if not self._started or self._last_beat is None:
                return True, None
            age = time.monotonic() - self._last_beat
            return age <= HEALTH_MAX_STALENESS_SECS, age


liveness = Liveness()


# ===========================================================================
# Regime tracker: lock-guarded projection over the feature stream.
# ===========================================================================

class RegimeTracker:
    """Maintains the rolling observation window, refits the HMM periodically,
    and answers the regime endpoints. Every mutation and every read snapshot is
    guarded by a single lock (the lock-guarded-tracker pattern from forseti)."""

    def __init__(self, n_states=N_STATES, window_size=WINDOW_SIZE,
                 min_obs=MIN_OBS_TO_FIT, refit_every=REFIT_EVERY,
                 n_features=len(FEATURE_NAMES), seed=None):
        self.lock = threading.Lock()
        self.n_states = int(n_states)
        self.n_features = int(n_features)
        self.window_size = int(window_size)
        self.min_obs = int(min_obs)
        self.refit_every = int(refit_every)
        self.seed = seed

        # Rolling raw (un-standardised) observations and their timestamps.
        self.window = deque(maxlen=self.window_size)   # each: (ts_str, np.array)
        self.obs_since_fit = 0
        self.total_obs = 0

        # Fitted model state.
        self.model = None
        self.scaler_mean = None
        self.scaler_std = None
        self.labels = None
        self.interp = None
        self.loglik = None
        self.fit_n_obs = 0
        self.fit_count = 0
        self.trained = False
        self._as_of = None

        # Recent per-observation regime assignments (causal / filtered).
        self.history = deque(maxlen=HISTORY_MAX)
        self.rejected = 0

    # -- ingestion ------------------------------------------------------------

    @staticmethod
    def _finite(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(v):
            return None
        return v

    def extract_vector(self, payload):
        """Build the observation vector from an obi-bridge feature event.

        Returns (ts_str, np.array([trend, volatility, |obi|])) or None when a
        required value is missing / non-finite. Tolerant of the canonical
        nested ``values`` shape and a few snake_case fallbacks.
        """
        if not isinstance(payload, dict):
            return None
        values = payload.get("values")
        if not isinstance(values, dict):
            values = payload  # allow a flattened variant
        trend = self._finite(
            values.get("momentum", values.get("momentum5m",
                       values.get("m5_momentum")))
        )
        vol = self._finite(values.get("volatility", values.get("vol")))
        obi = self._finite(values.get("obi", values.get("OBI")))
        if trend is None or vol is None or obi is None:
            return None
        ts = (
            payload.get("eventTime")
            or payload.get("event_time")
            or payload.get("timestamp")
        )
        if not isinstance(ts, str):
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        vec = np.array([trend, vol, abs(obi)], dtype=float)
        return ts, vec

    def add_observation(self, payload):
        """Ingest one feature event; refit if due; record the causal regime."""
        rec = self.extract_vector(payload)
        if rec is None:
            with self.lock:
                self.rejected += 1
            return
        ts, vec = rec
        with self.lock:
            self.window.append((ts, vec))
            self.obs_since_fit += 1
            self.total_obs += 1
            if self._as_of is None or ts > self._as_of:
                self._as_of = ts

            self._maybe_refit_locked()

            if self.trained and self.model is not None:
                self._record_current_regime_locked(ts)

    def warmstart(self, payloads):
        """Bulk-load historical feature payloads and fit ONCE at the end.

        Used for the Mimir warm-start. Feeding hundreds of records through
        add_observation would trigger a refit every REFIT_EVERY obs (slow start);
        here we append the whole batch under a single lock and fit exactly once.
        Records are expected in event-time order so the bounded window ends at the
        most-recent observation. Returns the number of records accepted.
        """
        accepted = 0
        with self.lock:
            for payload in payloads:
                rec = self.extract_vector(payload)
                if rec is None:
                    self.rejected += 1
                    continue
                ts, vec = rec
                self.window.append((ts, vec))
                self.total_obs += 1
                if self._as_of is None or ts > self._as_of:
                    self._as_of = ts
                accepted += 1
            if len(self.window) >= self.min_obs:
                try:
                    self._fit_locked()
                except Exception as e:   # never let a warm-start fit abort boot
                    log.error("HMM warm-start fit failed: %s", e)
                if self.trained and self.model is not None and self._as_of:
                    self._record_current_regime_locked(self._as_of)
        return accepted

    # -- fitting --------------------------------------------------------------

    def _window_matrix_locked(self):
        return np.array([v for (_, v) in self.window], dtype=float)

    def _standardise_locked(self, X):
        """z-score X against the current scaler (fit-time window statistics)."""
        return (X - self.scaler_mean) / self.scaler_std

    def _maybe_refit_locked(self):
        n = len(self.window)
        due = False
        if not self.trained:
            if n >= self.min_obs:
                due = True
        elif self.obs_since_fit >= self.refit_every:
            due = True
        if not due:
            return
        try:
            self._fit_locked()
        except Exception as e:   # never let a fit failure kill the consumer
            log.error("HMM refit failed: %s", e)

    def _fit_locked(self):
        X_raw = self._window_matrix_locked()
        mean = X_raw.mean(axis=0)
        std = X_raw.std(axis=0)
        std = np.where(std > 1e-9, std, 1.0)   # floor to avoid divide-by-zero
        Xs = (X_raw - mean) / std

        model = GaussianHMM(self.n_states, self.n_features)
        model.fit_best(Xs, n_init=EM_N_INIT, max_iter=EM_MAX_ITER, tol=EM_TOL,
                       seed=self.seed)
        labels, interp = derive_labels(model.means_, model.covars_)

        self.model = model
        self.scaler_mean = mean
        self.scaler_std = std
        self.labels = labels
        self.interp = interp
        self.loglik = float(model.score(Xs))
        self.fit_n_obs = X_raw.shape[0]
        self.fit_count += 1
        self.trained = True
        self.obs_since_fit = 0
        log.info(
            "HMM refit #%d on %d obs: loglik=%.2f labels=%s converged=%s",
            self.fit_count, self.fit_n_obs, self.loglik, self.labels,
            model.converged_,
        )

    # -- inference ------------------------------------------------------------

    def _record_current_regime_locked(self, ts):
        """Append the causal (forward-filtered) regime for the newest obs."""
        Xs = self._standardise_locked(self._window_matrix_locked())
        post = self.model.filter(Xs)          # (K,) last-step posterior
        state = int(np.argmax(post))
        self.history.append({
            "timestamp": ts,
            "state": state,
            "label": self.labels[state],
            "probability": round(float(post[state]), 6),
            "stateProbs": [round(float(p), 6) for p in post],
        })

    def get_regime(self):
        """Snapshot for GET /api/regime."""
        with self.lock:
            if not self.trained or self.model is None:
                return {
                    "trained": False,
                    "reason": (
                        "insufficient data: need >= {} observations to fit "
                        "(have {})".format(self.min_obs, len(self.window))
                    ),
                    "nStates": self.n_states,
                    "nObservations": len(self.window),
                    "features": list(FEATURE_NAMES),
                    "asOf": self._as_of,
                }
            Xs = self._standardise_locked(self._window_matrix_locked())
            post = self.model.filter(Xs)
            state = int(np.argmax(post))
            transmat = self.model.transmat_
            stat = stationary_distribution(transmat)
            return {
                "trained": True,
                "currentRegime": {
                    "id": state,
                    "label": self.labels[state],
                    "probability": round(float(post[state]), 6),
                },
                "stateProbs": [round(float(p), 6) for p in post],
                "nStates": self.n_states,
                "nObservations": len(self.window),
                "fitObservations": self.fit_n_obs,
                "refits": self.fit_count,
                "logLikelihood": round(float(self.loglik), 6),
                "transitionMatrix": [
                    [round(float(x), 6) for x in row] for row in transmat
                ],
                "stationary": [round(float(x), 6) for x in stat],
                "labels": list(self.labels),
                "features": list(FEATURE_NAMES),
                "asOf": self._as_of,
            }

    def get_history(self, limit=50):
        with self.lock:
            records = list(self.history)
            trained = self.trained
        recent = records[-limit:] if limit and limit > 0 else records
        return {
            "trained": trained,
            "count": len(recent),
            "history": list(reversed(recent)),
        }

    def get_model(self):
        """Snapshot for GET /api/regime/model."""
        with self.lock:
            if not self.trained or self.model is None:
                return {
                    "trained": False,
                    "reason": (
                        "insufficient data: need >= {} observations to fit "
                        "(have {})".format(self.min_obs, len(self.window))
                    ),
                    "nStates": self.n_states,
                    "features": list(FEATURE_NAMES),
                }
            m = self.model
            transmat = m.transmat_
            stat = stationary_distribution(transmat)
            states = []
            for k in range(self.n_states):
                cov = m.covars_[k]
                states.append({
                    "id": k,
                    "label": self.labels[k],
                    "interpretation": self.interp[k],
                    "mean": [round(float(x), 6) for x in m.means_[k]],
                    "covariance": [
                        [round(float(x), 6) for x in row] for row in cov
                    ],
                    "covarianceDiagonal": [
                        round(float(cov[i, i]), 6) for i in range(self.n_features)
                    ],
                    "stationaryProb": round(float(stat[k]), 6),
                })
            return {
                "trained": True,
                "nStates": self.n_states,
                "features": list(FEATURE_NAMES),
                "standardisation": {
                    "note": (
                        "features are z-scored against the fit-time rolling "
                        "window; means/covariances below are in standardised "
                        "units"
                    ),
                    "mean": [round(float(x), 6) for x in self.scaler_mean],
                    "std": [round(float(x), 6) for x in self.scaler_std],
                },
                "startProb": [round(float(x), 6) for x in m.startprob_],
                "transitionMatrix": [
                    [round(float(x), 6) for x in row] for row in transmat
                ],
                "stationary": [round(float(x), 6) for x in stat],
                "states": states,
                "logLikelihood": round(float(self.loglik), 6),
                "labelDerivation": (
                    "turbulence = volatility-feature mean + trace(Sigma)/D; "
                    "lowest -> calm, highest -> turbulent, strongest |trend| in "
                    "the middle band -> trending (deterministic, from fitted "
                    "parameters only)"
                ),
                "fitObservations": self.fit_n_obs,
                "refits": self.fit_count,
            }


tracker = RegimeTracker()


# ===========================================================================
# HTTP layer.
# ===========================================================================

class HeimdallHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/regime" or path == "/":
            self._json_response(tracker.get_regime())
        elif path == "/api/regime/history":
            limit = self._int_param(qs, "limit", 50, lo=1, hi=5000)
            self._json_response(tracker.get_history(limit=limit))
        elif path == "/api/regime/model":
            self._json_response(tracker.get_model())
        elif path == "/healthz" or path == "/readyz":
            ok, age = liveness.status()
            payload = {
                "status": "ok" if ok else "degraded",
                "service": "heimdall",
                "consumer_alive": ok,
                "consumer_last_beat_age_secs": (
                    round(age, 1) if age is not None else None
                ),
                "trained": tracker.trained,
                "nObservations": len(tracker.window),
            }
            self._json_response(payload, status=200 if ok else 503)
        else:
            self.send_error(404)

    @staticmethod
    def _int_param(qs, name, default, lo=None, hi=None):
        try:
            v = int(qs.get(name, [str(default)])[0])
        except (ValueError, TypeError):
            return default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _make_features_consumer(consumer_factory=KafkaConsumer):
    """Build the features consumer.

    A regime model needs training data, so Heimdall REPLAYS the feature topic
    from the start (auto_offset_reset="earliest", fresh group per process) to warm
    the HMM immediately rather than waiting ~40 min for a slow live feed to reach
    MIN_OBS. The bounded ROLLING window (WINDOW_SIZE) still keeps only the most
    recent observations, so replaying the backlog costs nothing extra and the
    fitted model stays "recent": the window fills from the tail of history, then
    the model tracks the live stream. The current regime is reported by causal
    forward-filtering over that window (never peeks at the future).
    """
    import uuid
    return consumer_factory(
        FEATURES_TOPIC,
        bootstrap_servers=KAFKA_BROKERS,
        group_id="heimdall-regime-{}".format(uuid.uuid4().hex),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )


def _http_get_json(url, timeout=8):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def backfill_from_mimir():
    """Warm-start the HMM from Mimir's persisted feature history.

    On a freshly-restarted stack the Kafka topic backlog is empty and the live
    feature feed is slow, so the model would sit at trained:false for many minutes.
    Mimir (the point-in-time feature store) persists real history, so we seed the
    rolling window from it and fit immediately. The fed records are REAL stored
    features in event-time order, so the fit is on genuine history, not synthetic
    data. Best-effort: any failure (Mimir down, empty, malformed) falls back
    silently to warming up from the live stream.
    """
    if not MIMIR_BACKFILL:
        log.info("Mimir warm-start disabled; warming from live stream only")
        return
    import urllib.parse
    src = None
    for attempt in range(10):                  # mimir may still be booting
        try:
            src = _http_get_json(MIMIR_URL + "/api/sources")
            break
        except Exception as e:
            log.info("Mimir warm-start: sources not ready (attempt %d/10 at %s): %s",
                     attempt + 1, MIMIR_URL, e)
            time.sleep(3)
    if src is None:
        log.warning("Mimir warm-start skipped (unreachable); warming from live stream")
        return
    sources = src.get("sources") if isinstance(src, dict) else None
    instruments = sorted({
        s.get("instrument") for s in (sources or [])
        if isinstance(s, dict) and s.get("instrument")
    })
    if not instruments:
        log.info("Mimir warm-start: no instruments registered yet; warming from live")
        return

    rows = []
    for inst in instruments:
        try:
            url = "%s/api/features/history?instrument=%s&limit=%d" % (
                MIMIR_URL, urllib.parse.quote(inst), MIMIR_BACKFILL_LIMIT)
            data = _http_get_json(url)
            batch = data.get("rows") or data.get("features") or []
            rows.extend(r for r in batch if isinstance(r, dict))
        except Exception as e:
            log.warning("Mimir warm-start: history fetch failed for %s: %s", inst, e)

    if not rows:
        log.info("Mimir warm-start: no history returned; warming from live stream")
        return

    def _event_time(r):
        return (r.get("event_time")
                or (r.get("feature") or {}).get("eventTime")
                or "")

    rows.sort(key=_event_time)                 # oldest -> newest
    rows = rows[-WINDOW_SIZE:]                  # keep only what the window can hold
    payloads = [r["feature"] for r in rows
                if isinstance(r.get("feature"), dict)]

    accepted = tracker.warmstart(payloads)
    log.info(
        "Mimir warm-start: fed %d/%d historical obs from %d instruments "
        "(trained=%s, window=%d)",
        accepted, len(payloads), len(instruments),
        tracker.trained, len(tracker.window))


def consume_features():
    for attempt in range(30):
        try:
            consumer = _make_features_consumer()
            log.info("Connected to Kafka, consuming %s", FEATURES_TOPIC)
            break
        except KafkaConnectionError:
            log.warning("Kafka not ready (attempt %d/30)...", attempt + 1)
            time.sleep(2)
    else:
        log.error("Failed to connect to Kafka")
        return

    liveness.mark_started()

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            liveness.beat()
            for tp, messages in records.items():
                for msg in messages:
                    try:
                        payload = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        log.warning("Dropping undecodable feature record: %s", de)
                        continue
                    tracker.add_observation(payload)
        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  HEIMDALL — Market Regime Detection (Gaussian HMM / Baum-Welch)")
    log.info("=" * 60)
    log.info("  Features topic: %s", FEATURES_TOPIC)
    log.info("  States (K):     %d", N_STATES)
    log.info("  Window:         %d obs  (min to fit: %d, refit every: %d)",
             WINDOW_SIZE, MIN_OBS_TO_FIT, REFIT_EVERY)
    log.info("  Features:       %s", ", ".join(FEATURE_NAMES))
    log.info("  API port:       %d", PORT)
    log.info("  Endpoints:")
    log.info("    /api/regime          — current regime (forward-filtered)")
    log.info("    /api/regime/history  — recent regime assignments")
    log.info("    /api/regime/model    — fitted params + label derivation")
    log.info("    /healthz             — liveness")
    log.info("  NOTE: simulation only; no measured out-of-sample edge (PBO=1.0)")
    log.info("=" * 60)

    # Warm-start from Mimir's persisted history so the model is trained on boot
    # instead of waiting minutes for the live feed to reach MIN_OBS. Best-effort.
    try:
        backfill_from_mimir()
    except Exception as e:
        log.warning("Mimir warm-start raised (continuing live-only): %s", e)

    consumer_thread = threading.Thread(target=consume_features, daemon=True)
    consumer_thread.start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), HeimdallHandler)
    server.timeout = 1
    log.info("Heimdall HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Heimdall shutdown complete")


if __name__ == "__main__":
    main()
