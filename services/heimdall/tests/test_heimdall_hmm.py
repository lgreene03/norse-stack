"""Correctness tests for Heimdall's Gaussian HMM regime detector.

Run with: python3 -m pytest services/heimdall/tests/ -q  (kafka is stubbed in
conftest; numpy only, NO Kafka). Everything is driven by synthetic data sampled
from a KNOWN model, so the tests are self-contained and deterministic (fixed
seeds).

These tests are the correctness guardrail. In order:
  1. SYNTHETIC PARAMETER RECOVERY — fit recovers a known model's means +
     transition matrix, up to state permutation.
  2. MONOTONE LIKELIHOOD — Baum-Welch log-likelihood is non-decreasing across
     EM iterations (the EM guarantee).
  3. FORWARD-BACKWARD CONSISTENCY — gammas sum to 1 at each t; forward total
     log-likelihood equals the backward total.
  4. VITERBI — decoded path matches the true hidden path (up to permutation) on
     a clearly-separated sequence.
  5. INSUFFICIENT DATA -> trained:false; label derivation is deterministic.
"""

import itertools

import numpy as np

import heimdall
from heimdall import GaussianHMM, derive_labels, stationary_distribution


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _known_model(means, covars, transmat, startprob, covar_reg=1e-6):
    """Construct a GaussianHMM with fixed parameters to sample from."""
    K, D = np.asarray(means).shape
    m = GaussianHMM(K, D, covar_reg=covar_reg)
    m.means_ = np.asarray(means, dtype=float)
    m.covars_ = np.asarray(covars, dtype=float)
    m.transmat_ = np.asarray(transmat, dtype=float)
    m.startprob_ = np.asarray(startprob, dtype=float)
    return m


def _best_permutation(true_means, fitted_means):
    """Return the permutation p such that fitted_means[p] best matches
    true_means (minimising total squared mean distance). Small K -> brute force.
    """
    K = true_means.shape[0]
    best_perm = None
    best_cost = np.inf
    for perm in itertools.permutations(range(K)):
        cost = np.sum((fitted_means[list(perm)] - true_means) ** 2)
        if cost < best_cost:
            best_cost = cost
            best_perm = list(perm)
    return best_perm


# A well-separated 3-state model. Means are far apart relative to the emission
# spread, so the states are cleanly identifiable.
TRUE_MEANS = np.array([
    [-5.0, -5.0],
    [0.0, 5.0],
    [5.0, 0.0],
])
TRUE_COVARS = np.array([
    np.eye(2) * 0.5,
    np.eye(2) * 0.5,
    np.eye(2) * 0.5,
])
TRUE_TRANSMAT = np.array([
    [0.90, 0.05, 0.05],
    [0.10, 0.85, 0.05],
    [0.05, 0.10, 0.85],
])
TRUE_STARTPROB = np.array([1.0, 0.0, 0.0])


# ---------------------------------------------------------------------------
# (1) Synthetic parameter recovery — the gold-standard test.
# ---------------------------------------------------------------------------

def test_synthetic_parameter_recovery():
    rng = np.random.default_rng(20260720)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    X, _states = truth.sample(4000, rng=rng)

    model = GaussianHMM(3, 2)
    model.fit_best(X, n_init=6, max_iter=200, tol=1e-5, seed=7)

    perm = _best_permutation(TRUE_MEANS, model.means_)
    rec_means = model.means_[perm]
    rec_trans = model.transmat_[np.ix_(perm, perm)]

    mean_err = np.max(np.abs(rec_means - TRUE_MEANS))
    trans_err = np.max(np.abs(rec_trans - TRUE_TRANSMAT))

    assert mean_err < 0.25, "mean recovery error {:.4f} too large".format(mean_err)
    assert trans_err < 0.08, (
        "transition recovery error {:.4f} too large".format(trans_err)
    )

    # Recovered covariances should be close to the true 0.5*I too.
    rec_covars = model.covars_[perm]
    cov_err = np.max(np.abs(rec_covars - TRUE_COVARS))
    assert cov_err < 0.2, "covariance recovery error {:.4f} too large".format(cov_err)


# ---------------------------------------------------------------------------
# (2) Monotone likelihood — the EM guarantee.
# ---------------------------------------------------------------------------

def test_monotone_loglikelihood():
    rng = np.random.default_rng(11)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    X, _ = truth.sample(2000, rng=rng)

    model = GaussianHMM(3, 2)
    model.fit(X, max_iter=100, tol=1e-6, rng=np.random.default_rng(3))

    hist = np.array(model.loglik_history_)
    assert len(hist) >= 2
    diffs = np.diff(hist)
    # Allow a tiny negative tolerance for floating-point noise only.
    assert np.all(diffs >= -1e-6), (
        "log-likelihood decreased during EM: min step {:.3e}".format(diffs.min())
    )


# ---------------------------------------------------------------------------
# (3) Forward-backward consistency.
# ---------------------------------------------------------------------------

def test_forward_backward_consistency():
    rng = np.random.default_rng(99)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    X, _ = truth.sample(500, rng=rng)

    model = GaussianHMM(3, 2)
    model.fit_best(X, n_init=3, max_iter=100, tol=1e-5, seed=1)

    log_emission = model._log_emission(X)
    log_alpha, fwd_ll = model._forward(log_emission)
    log_beta = model._backward(log_emission)

    # gammas sum to 1 at every t.
    log_gamma = log_alpha + log_beta
    log_gamma -= heimdall._logsumexp(log_gamma, axis=1)[:, None]
    gamma = np.exp(log_gamma)
    sums = gamma.sum(axis=1)
    assert np.allclose(sums, 1.0, atol=1e-8), (
        "gamma rows not normalised: max dev {:.3e}".format(np.max(np.abs(sums - 1)))
    )

    # Backward total log-likelihood == forward total log-likelihood.
    log_startprob = np.log(model.startprob_)
    bwd_ll = heimdall._logsumexp(log_startprob + log_emission[0] + log_beta[0])
    assert abs(fwd_ll - bwd_ll) < 1e-6, (
        "forward LL {:.6f} != backward LL {:.6f}".format(fwd_ll, bwd_ll)
    )

    # score() must agree with the forward total, and with predict_proba's
    # implied normaliser at t=0.
    assert abs(model.score(X) - fwd_ll) < 1e-9

    # Un-normalised alpha*beta at ANY t sums (in log) to the same loglik.
    for t in (0, 123, 499):
        ll_t = heimdall._logsumexp(log_alpha[t] + log_beta[t])
        assert abs(ll_t - fwd_ll) < 1e-6


# ---------------------------------------------------------------------------
# (4) Viterbi decoding accuracy.
# ---------------------------------------------------------------------------

def test_viterbi_recovers_hidden_path():
    rng = np.random.default_rng(2026)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    X, true_states = truth.sample(3000, rng=rng)

    model = GaussianHMM(3, 2)
    model.fit_best(X, n_init=6, max_iter=200, tol=1e-5, seed=5)

    path, _logprob = model.decode(X)

    # Align fitted state ids to the truth by nearest mean, then map the decoded
    # path through the same permutation before scoring accuracy.
    perm = _best_permutation(TRUE_MEANS, model.means_)
    # perm[j] = fitted state that matches true state j; invert to map fitted->true
    inv = np.empty(3, dtype=int)
    for true_state, fitted_state in enumerate(perm):
        inv[fitted_state] = true_state
    mapped = inv[path]

    acc = np.mean(mapped == true_states)
    assert acc > 0.95, "Viterbi accuracy {:.3f} too low".format(acc)


# ---------------------------------------------------------------------------
# (5) Insufficient data + deterministic label derivation.
# ---------------------------------------------------------------------------

def _feature_event(momentum, volatility, obi, ts="2026-07-20T00:00:00Z"):
    """A feature event shaped like obi-bridge's features.obi.v1 payload."""
    return {
        "eventTime": ts,
        "instrument": "BTC-USDT",
        "values": {
            "momentum": momentum,
            "volatility": volatility,
            "obi": obi,
        },
    }


def test_insufficient_data_reports_untrained():
    trk = heimdall.RegimeTracker(n_states=3, window_size=1000, min_obs=200,
                                 refit_every=50, seed=0)
    rng = np.random.default_rng(0)
    for _ in range(50):  # below min_obs
        trk.add_observation(_feature_event(
            float(rng.normal()), float(abs(rng.normal())), float(rng.uniform(-1, 1))
        ))
    snap = trk.get_regime()
    assert snap["trained"] is False
    assert "reason" in snap
    assert snap["nObservations"] == 50
    assert snap["features"] == heimdall.FEATURE_NAMES

    model_snap = trk.get_model()
    assert model_snap["trained"] is False

    hist = trk.get_history()
    assert hist["count"] == 0


def test_label_derivation_is_deterministic_and_from_params():
    # State 0: low volatility mean + tight covariance -> should be "calm".
    # State 1: strong trend mean, moderate volatility -> "trending".
    # State 2: high volatility mean + wide covariance -> "turbulent".
    means = np.array([
        [0.0, -1.5, 0.2],   # calm: lowest volatility feature (index 1)
        [2.0, 0.0, 0.5],    # trending: strong |trend| (index 0)
        [0.1, 2.0, 1.5],    # turbulent: highest volatility feature
    ])
    covars = np.array([
        np.eye(3) * 0.2,
        np.eye(3) * 0.5,
        np.eye(3) * 2.0,
    ])
    labels_a, interp_a = derive_labels(means, covars)
    labels_b, interp_b = derive_labels(means, covars)

    # Deterministic: identical inputs -> identical outputs.
    assert labels_a == labels_b
    assert interp_a == interp_b

    assert labels_a[0] == "calm"
    assert labels_a[2] == "turbulent"
    assert labels_a[1] == "trending"

    # The derivation is a pure function of the fitted params: permuting the
    # state order permutes the labels correspondingly.
    perm = [2, 0, 1]
    labels_p, _ = derive_labels(means[perm], covars[perm])
    expected = [labels_a[i] for i in perm]
    assert labels_p == expected


def test_tracker_trains_and_reports_regime_on_synthetic_stream():
    """End-to-end: feed a synthetic regime-switching stream through the tracker
    and confirm it fits and reports a valid current regime with sane shapes."""
    trk = heimdall.RegimeTracker(n_states=3, window_size=1000, min_obs=200,
                                 refit_every=50, seed=42)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    rng = np.random.default_rng(123)
    X, _ = truth.sample(600, rng=rng)
    for row in X:
        # map the 2-D synthetic sample into the 3 feature slots the tracker reads
        trk.add_observation(_feature_event(float(row[0]), float(row[1]),
                                           float(np.tanh(row[0] * 0.1))))

    snap = trk.get_regime()
    assert snap["trained"] is True
    assert 0 <= snap["currentRegime"]["id"] < 3
    assert snap["currentRegime"]["label"] in {"calm", "trending", "turbulent",
                                              "choppy"}
    assert 0.0 <= snap["currentRegime"]["probability"] <= 1.0
    # Tolerances allow for the 6-dp rounding applied to the JSON output.
    assert abs(sum(snap["stateProbs"]) - 1.0) < 1e-5
    # Transition matrix rows are stochastic.
    for row in snap["transitionMatrix"]:
        assert abs(sum(row) - 1.0) < 1e-5
    # Stationary distribution is a valid distribution.
    assert abs(sum(snap["stationary"]) - 1.0) < 1e-5

    model_snap = trk.get_model()
    assert model_snap["trained"] is True
    assert len(model_snap["states"]) == 3

    hist = trk.get_history(limit=10)
    assert hist["trained"] is True
    assert hist["count"] > 0


def test_stationary_distribution_is_left_eigenvector():
    A = np.array([
        [0.9, 0.1],
        [0.2, 0.8],
    ])
    pi = stationary_distribution(A)
    # pi A == pi and sums to 1.
    assert abs(pi.sum() - 1.0) < 1e-9
    assert np.allclose(pi @ A, pi, atol=1e-8)
    # Closed form for a 2-state chain: pi = [b/(a+b), a/(a+b)] where a=P(0->1),
    # b=P(1->0).
    a, b = 0.1, 0.2
    assert np.allclose(pi, [b / (a + b), a / (a + b)], atol=1e-8)


# ---------------------------------------------------------------------------
# (7) Bulk warm-start (Mimir backfill path).
# ---------------------------------------------------------------------------

def _ts(i):
    """Distinct, strictly-increasing ISO timestamps for ordered warm-start."""
    return "2026-07-20T{:02d}:{:02d}:00Z".format(i // 60, i % 60)


def test_warmstart_bulk_loads_and_fits_once():
    """warmstart() should append a whole batch and fit exactly once, leaving the
    tracker trained with a valid regime — the Mimir backfill fast-path."""
    trk = heimdall.RegimeTracker(n_states=3, window_size=1000, min_obs=200,
                                 refit_every=50, seed=42)
    truth = _known_model(TRUE_MEANS, TRUE_COVARS, TRUE_TRANSMAT, TRUE_STARTPROB)
    rng = np.random.default_rng(2026)
    X, _ = truth.sample(600, rng=rng)
    payloads = [
        _feature_event(float(row[0]), float(row[1]),
                       float(np.tanh(row[0] * 0.1)), ts=_ts(i))
        for i, row in enumerate(X)
    ]

    accepted = trk.warmstart(payloads)
    assert accepted == 600

    # Fitted in one shot (fit_count == 1: warmstart fits ONCE, not per-obs).
    assert trk.fit_count == 1
    snap = trk.get_regime()
    assert snap["trained"] is True
    assert snap["nObservations"] == 600
    assert 0 <= snap["currentRegime"]["id"] < 3
    assert snap["currentRegime"]["label"] in {"calm", "trending", "turbulent",
                                              "choppy"}
    # Current regime is anchored to the most-recent (last) warm-start observation.
    assert snap["asOf"] == _ts(599)


def test_warmstart_below_min_obs_stays_untrained():
    """A warm-start batch below min_obs must be accepted but leave the model
    untrained (no meaningless fit on a handful of points)."""
    trk = heimdall.RegimeTracker(n_states=3, window_size=1000, min_obs=200,
                                 refit_every=50, seed=0)
    rng = np.random.default_rng(1)
    payloads = [
        _feature_event(float(rng.normal()), float(abs(rng.normal())),
                       float(rng.uniform(-1, 1)), ts=_ts(i))
        for i in range(50)
    ]
    accepted = trk.warmstart(payloads)
    assert accepted == 50
    assert trk.get_regime()["trained"] is False
    assert trk.fit_count == 0


def test_warmstart_skips_malformed_payloads():
    """Malformed records (missing values) must be rejected, not crash the batch."""
    trk = heimdall.RegimeTracker(n_states=3, window_size=1000, min_obs=200,
                                 refit_every=50, seed=0)
    good = [_feature_event(0.1, 0.2, 0.3, ts=_ts(i)) for i in range(5)]
    bad = [{"eventTime": _ts(100), "instrument": "BTC-USDT"},   # no "values"
           {"nonsense": True}]
    accepted = trk.warmstart(good + bad)
    assert accepted == 5          # only the well-formed events counted
