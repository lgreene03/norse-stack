# Service Level Objectives (SLOs)

This document defines the Norse Stack SLOs, their error budgets, and how the
multi-burn-rate alerts in [`monitoring/alerts/slo.yml`](../monitoring/alerts/slo.yml)
enforce them. The base service-liveness/fill-quality alerts live separately in
[`monitoring/alerts/norse-stack.yml`](../monitoring/alerts/norse-stack.yml); the
SLO rules add *budget-aware* paging on top of those raw liveness checks.

> Norse Stack is a local trading **simulation** / portfolio project. The
> numbers below are illustrative-but-defensible targets for the demo, chosen so
> the alerting story is real (multi-burn-rate, fast+slow windows) rather than
> production SLAs negotiated with a customer.

## Why multi-burn-rate

A single static threshold on an SLI either pages on every transient blip (noisy,
ignored) or waits so long that the budget is already gone. The Google SRE
workbook (ch. 5, "Alerting on SLOs") recommends **burn-rate** alerting: measure
how fast the error budget is being consumed relative to the rate that would
exhaust it exactly at the end of the window.

Each SLO below uses **two paired alerts**:

- **Fast burn** — long window `1h` gated by short window `5m`. Threshold `14.4×`
  ≈ 2% of a 30-day budget consumed in 1 hour. Pages (`critical`); catches
  budget-destroying spikes within minutes.
- **Slow burn** — long window `6h` gated by short window `30m`. Threshold `6×` ≈
  5% of a 30-day budget consumed in 6 hours. Warns; catches sustained low-grade
  degradation that a fast-burn alert would miss.

The short "gate" window is ANDed with the long window so the alert clears
quickly once the burn stops (hysteresis), avoiding a stuck-firing alert after
recovery.

## SLO catalogue

| # | SLO | SLI | Target | Error budget (30d) | Alerts |
|---|-----|-----|--------|--------------------|--------|
| 1 | **Signal→decision latency** | fraction of bridge-signal→huginn-decision events with latency ≤ 256ms | 99% | 1% | `HuginnLatencySLOFastBurn` / `…SlowBurn` |
| 2 | **Feature freshness** | feature events continue to be processed (non-zero rate) | no gap > 5m (fast) / 30m (slow) | — (gap-based) | `FeatureFreshnessFastBurn` / `…SlowBurn` |
| 3 | **Fill freshness** | fills continue to be executed (non-zero rate) | no gap > 5m (fast) / 20m (slow) | — (gap-based) | `FillFreshnessFastBurn` / `…SlowBurn` |
| 4 | **Core readiness** | `up` for huginn, sleipnir, muninn | 99.5% | 0.5% | `CoreReadinessSLOFastBurn` / `…SlowBurn` |

### 1. Signal→decision latency SLO

- **Series:** `huginn_signal_to_decision_ms` (histogram). The SLI uses the
  `le="256"` bucket vs `_count` as the "good ≤256ms" ratio.
- **Why 256ms, not 250ms:** the histogram buckets are exponential powers of two
  (`ExponentialBuckets(1, 2, 12)`), so the nearest real bucket boundary to the
  250ms target is `le="256"`. Picking an existing boundary keeps the SLI exact
  rather than interpolating across buckets.
- **Budget math:** target 99% ⇒ budget 0.01. Fast threshold `14.4 × 0.01 =
  0.144`; slow threshold `6 × 0.01 = 0.06`. Recording rules
  `huginn:s2d_latency_bad_ratio_rate{5m,30m,1h,6h}` precompute the bad-event
  ratio per window.

### 2. Feature freshness SLO

- **Series:** `huginn_feature_event_age_seconds_count` (processing rate) gated
  by `huginn_features_consumed_total`. A zero processing rate is a hard SLI
  miss — the strategy is starved.
- Gap-based rather than ratio-based: for a continuously-streaming pipeline the
  meaningful budget is "how long may the stream stall", so the fast/slow split
  is encoded as the `5m`/`30m` evaluation windows plus `for:` durations.

### 3. Fill freshness SLO

- **Series:** `huginn_fills_executed_total`.
- **Caveat:** the cost-aware gate (`COST_HURDLE_K`) can legitimately suppress
  *all* fills during a low-edge period, so a quiet fill path is not always a
  fault. The fast-burn alert is `warning` (not `critical`) and its description
  says to correlate with `huginn_orders_cost_suppressed_total`; only the
  sustained 20m slow-burn escalates to `critical`.

### 4. Core readiness SLO

- **Series:** `up{job=~"huginn|sleipnir|muninn"}`.
- **Budget math:** target 99.5% ⇒ budget 0.005. Fast threshold `14.4 × 0.005 =
  0.072`; slow `6 × 0.005 = 0.03`. Recording rules
  `norse:core_unavailable_ratio_rate{5m,30m,1h,6h}` precompute `1 - avg(up)` per
  window; the alert takes `max(...)` so any single unhealthy service trips it.

## Operating the budgets

- **Budget remaining** for SLO #1 over the trailing 30d:
  `1 - (1 - 0.01 - avg_over_time(huginn:s2d_latency_bad_ratio_rate1h[30d])) …` —
  in practice read it off the Grafana SLO panel rather than by hand.
- **When fast-burn pages:** treat as an incident — find the regression (a slow
  consumer, GC pause, Kafka lag) and stop the bleed.
- **When slow-burn warns:** schedule the fix within the budget window; you have
  headroom but the trend is adverse.
- **Budget exhausted:** freeze risk-adding changes to the offending service
  until the trailing-window SLI recovers (error-budget policy).

## Validation

```sh
docker run --rm --entrypoint promtool \
  -v "$PWD/monitoring/alerts:/alerts" prom/prometheus:v2.53.0 \
  check rules /alerts/slo.yml /alerts/norse-stack.yml
```

Prometheus loads both files via the `rule_files: /etc/prometheus/alerts/*.yml`
glob in [`monitoring/prometheus.yml`](../monitoring/prometheus.yml).
