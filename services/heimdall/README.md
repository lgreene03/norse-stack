# Heimdall — Market Regime Detection (Gaussian HMM / Baum-Welch)

Heimdall is the Norse Stack's regime-detection service. It watches the live
feature stream (`features.obi.v1`) that obi-bridge emits and fits a multivariate
**Gaussian Hidden Markov Model** with the **Baum-Welch (EM)** algorithm to infer
the latent market regime the tape is currently in.

This is a deliberate homage to Renaissance Technologies / Jim Simons: Leonard
Baum, co-inventor of the Baum-Welch algorithm, worked *at* Renaissance, and
hidden Markov models are part of the folklore of how Medallion read hidden
market states. The mathematics here is implemented honestly and from first
principles in pure numpy: forward-backward in **log space** for numerical
stability, Baum-Welch/EM training with **covariance regularisation**, **Viterbi**
decoding, and exact log-likelihood.

## Honest framing

This is a trading **simulation** with **no measured out-of-sample edge** — the
stack's own research gateway reports PBO = 1.00. Heimdall detects and labels
statistical regimes; it makes **no claim** that trading those regimes is
profitable. The regime labels are **derived from the fitted emission
parameters**, never hardcoded, and the derivation is exposed under
`/api/regime/model`. Until there is enough data to fit a model, `/api/regime`
returns `{"trained": false}` and never fabricates a regime.

## The model

A Gaussian HMM with `K` latent states (default 3). Each state `k` emits
observations from a multivariate normal `N(mu_k, Sigma_k)`; states evolve under
a row-stochastic transition matrix `A`.

* **Forward-backward** (`_forward` / `_backward`) run entirely in log space with
  a stable `logsumexp`, so products of thousands of small emission likelihoods
  never underflow.
* **Baum-Welch / EM** (`fit`) alternates an E-step (posterior state
  responsibilities `gamma` and transition counts `xi`) with an M-step
  (closed-form re-estimation of `startprob`, `A`, `means`, `covars`). Each
  M-step adds a covariance floor (`COVAR_REG` × mean feature variance) so a
  state cannot collapse onto a handful of points and produce a singular or
  exploding covariance. EM only finds a *local* optimum, so `fit_best` runs
  several random restarts (k-means++-style mean seeding) and keeps the highest
  final log-likelihood.
* **Viterbi** (`decode`) returns the single most-likely state path.
* **Filtering** (`filter`) returns the causal, forward-only last-step posterior
  `P(state_T | x_1..T)` — this is what the live current regime is reported from,
  so it never peeks at the future.

## Observation vector

Three well-motivated features, each z-scored against the current rolling window
before fitting (so the fitted means/covariances are in standardised units,
documented in `/api/regime/model`):

| Feature | Source (`values.*`) | Why |
|---|---|---|
| `trend_m5_momentum` | `momentum` | signed 5-minute EMA-crossover momentum — separates trending from range-bound |
| `volatility_atr` | `volatility` | ATR-based realised volatility — the classic calm-vs-turbulent driver |
| `abs_obi_imbalance` | `abs(obi)` | order-book imbalance *intensity* — rises in stressed / one-sided books |

A model is (re)fit over the rolling window (default last 1000 obs): the first
fit happens once there are `HEIMDALL_MIN_OBS` observations (default 200), then
every `HEIMDALL_REFIT_EVERY` new observations (default 50).

## Regime labels (derived, not hardcoded)

`derive_labels` is a deterministic pure function of the fitted parameters:

* `turbulence(k) = mean of the volatility feature + trace(Sigma_k)/D`
* `trend_strength(k) = |mean of the trend feature|`
* The lowest-turbulence state is **calm**, the highest is **turbulent**; the
  strongest-trend state in the middle band is **trending** (else **choppy**).

The same fitted parameters always yield the same labels, and the full derivation
(scores per state) is exposed under `/api/regime/model`.

## Endpoints (`HEIMDALL_PORT`, default 8097)

* `GET /api/regime` — current regime (forward-filtered), state posteriors,
  transition matrix, stationary distribution, log-likelihood, feature names,
  `asOf`. Returns `{"trained": false, "reason": ...}` until enough data.
* `GET /api/regime/history?limit=N` — recent causal (filtered) regime
  assignments with timestamps.
* `GET /api/regime/model` — fitted means, covariances (full + diagonal),
  transition matrix, stationary distribution, per-state label + interpretation,
  and the label-derivation description.
* `GET /healthz` — liveness (503 if the consumer thread has wedged).

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `HEIMDALL_PORT` | `8097` | HTTP port |
| `HEIMDALL_N_STATES` | `3` | number of latent regimes `K` |
| `HEIMDALL_WINDOW` | `1000` | rolling observation window |
| `HEIMDALL_MIN_OBS` | `200` | observations before the first fit |
| `HEIMDALL_REFIT_EVERY` | `50` | refit cadence (new obs) |
| `HEIMDALL_EM_MAX_ITER` | `100` | max EM iterations per restart |
| `HEIMDALL_EM_N_INIT` | `5` | random restarts per fit |
| `HEIMDALL_COVAR_REG` | `1e-3` | covariance floor (× mean feature variance) |
| `KAFKA_BROKERS` | `redpanda:29092` | Kafka bootstrap |
| `FEATURES_TOPIC` | `features.obi.v1` | feature stream |

## Tests

```bash
cd norse-stack
python3 -m pytest services/heimdall/tests/ -q
```

The suite is numpy-only and needs no Kafka: it samples from a known Gaussian HMM
and checks (1) parameter recovery up to state permutation, (2) monotone EM
log-likelihood, (3) forward-backward consistency, (4) Viterbi path accuracy, and
(5) the untrained / deterministic-label guarantees.
