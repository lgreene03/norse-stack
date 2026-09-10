# ROADMAP — Book-True Replay Parity

Phased delivery. Each phase ends with a working, tested, documented increment,
and no phase is marked done until it passes the quality gate at the bottom of
this file. Phases are not skipped.

## Why this roadmap exists

The README's first claim under "Why this isn't a toy" is **deterministic replay
parity**: that Muninn's feature engine produces byte-identical output from the
same input events, so a backtest and a live run share one computation path.

For the *engine* that is true and enforced. For the *signal* it is not, and on
2026-09-10 that gap was measured:

| | Live (`services/obi-bridge/bridge.py:274`) | Backtest (`huginn/cmd/fetcher`) |
|---|---|---|
| `obi` | `(bid_vol - ask_vol) / total` from **L2 book depth** | `(BuyVol - SellVol) / total` from **executed trades** |

These are different quantities sharing a name: book *state* versus realised
*flow*. Two further features are degenerate in the backtest path — `vpin` is
computed as exactly `|obi|`, carrying no independent information, and
`microPrice` is assigned the same value as `vwap`.

The consequences cut both ways, and both are bad:

1. `docs/EDGE_VERDICT.md` rejects "OBI" on out-of-sample evidence. That verdict
   is really about signed taker flow, not about order-book imbalance.
2. The live stack trades a signal that has **never been through the
   walk-forward gate at all**.

**The goal of this roadmap is to make the headline claim true at the signal
level, and then to re-run the validation gate honestly against the real
signal.** That is the novel part: book-level replay parity, enforced by a test
rather than asserted in a README, with the resulting verdict published whether
it flatters the project or not.

## What is already established

Do not re-derive these; they are measured, not assumed.

- Bulk history loads from `data.binance.vision` at ~1.5M trades in 6 seconds,
  against 8+ minutes per day for the paged REST endpoint (`huginn`,
  `fetcher --source vision`). Rebuilding 2026-05-18 from bulk dumps reproduces
  the committed `btc_test.jsonl` on **1440/1440 rows exactly**.
- On a 6-month window (264,961 minute-bars vs the fixture's 1,440) the OBI
  verdict is unchanged at **0/4 profitable OOS folds**, but the statistics that
  were degenerate now resolve: OOS Sharpe moves from `0.0000 ± 0.0000` to
  **−19.29 ± 10.54**, Deflated Sharpe computes on 4/4 folds with
  P(true Sharpe ≤ 0) = 0.9995, and **PBO moves from 1.0000 to 0.0000**.
- That PBO move is not good news and must not be reported as such. PBO asks
  whether the in-sample-best config lands in the OOS bottom half. At 0.00 it
  never did, which means config selection was never the failure — every config
  loses. PBO cannot detect overfitting when nothing is profitable to overfit to.
- On six months the strategy's **in-sample** Sharpe is also strongly negative
  (−10.0 to −10.5). The "promising in-sample, destroyed by walk-forward" trap
  described in `EDGE_VERDICT.md` was itself an artefact of the 24h window.
- `internal/research/research.go:402` in huginn hardcodes `hitRate: 0`, so
  walk-forward's reported `Hit: 0.0%` is not a measurement. Do not cite it.

---

## Phase P1 — Historical order-book depth 🟡

**Goal.** Obtain real historical L2 depth, so a replay can compute the same
quantity the live path computes.

**Deliverables.**
- Loader for Binance futures UM `bookDepth` daily dumps (confirmed available:
  `data/futures/um/daily/bookDepth/<SYMBOL>/` returns HTTP 206).
- Same shared-aggregator discipline as `fetcher --source vision`: one code path
  folds records, so outputs stay comparable across sources.
- Parser tests covering the dump's real layout, including timestamp units.

**Exit criteria.** A dated range of book snapshots loads reproducibly, and a
second run over the same range is byte-identical to the first.

**Note.** This moves instrument from spot to perpetual. That is a real change in
what is being measured and must be stated wherever results are published, not
glossed.

---

## Phase P2 — One feature-computation path 🔴

**Goal.** Delete the second implementation. Live and replay must call the *same*
code to compute `obi`, not two functions that happen to share a name.

**Deliverables.**
- Feature computation extracted into a single library consumed by both the live
  `obi-bridge` path and the replay path.
- `vpin` implemented as a genuine volume-bucketed order-flow toxicity measure
  rather than `|obi|`.
- `microPrice` implemented as a genuine book-weighted quote
  (`(bidPx·askQty + askPx·bidQty) / (bidQty + askQty)`) rather than an alias of
  `vwap`.

**Exit criteria.** Grep proves exactly one implementation of each feature.

---

## Phase P3 — Parity enforced by test 🔴

**Goal.** Make the README's claim falsifiable in CI.

**Deliverables.**
- A parity test that feeds one recorded book-event sequence through the live
  path and the replay path and asserts **byte-identical** feature output.
- The test runs in CI on every PR, not on a schedule.
- A deliberate one-sided change to either path must fail it. Prove that by
  breaking it once, observing the failure, and reverting.

**Exit criteria.** CI fails when the two paths disagree. Until that has been
demonstrated, this phase is not done.

---

## Phase P4 — Re-run the gate on the real signal 🔴

**Goal.** Put true order-book imbalance through the same walk-forward + PBO gate
that rejected the trade-flow proxy.

**Deliverables.**
- Walk-forward + PBO + Deflated Sharpe over a multi-month book-depth window.
- Results published in `EDGE_VERDICT.md` and `RESULTS.md` **alongside** the
  existing 24h and 6-month proxy results, not replacing them.

**Exit criteria.** A published verdict on the real signal, reported honestly
whichever way it falls. A positive result is not the exit criterion; an honest,
reproducible one is.

---

## Phase P5 — Supersede the published figures 🔴

**Goal.** The README and `EDGE_VERDICT.md` currently headline **PBO = 1.00**
from the 24h fixture. On better data that figure is 0.00 and means something
different. Leaving it unqualified is the kind of stale-number problem this
project exists to avoid.

**Exit criteria.** Every published statistic states its window, its instrument,
and which signal it measured.

---

## Quality gate — applies to every phase

A phase is not done until all of these pass. "It builds" is not done.

- **Tests.** `go test ./...` in huginn (17 packages), the eight service suites
  in norse-stack (190 tests), all green. New behaviour ships with new tests.
- **Lint.** `ruff check` clean against the **pinned** version in
  `requirements-dev.txt`. Never unpin to make a failure go away.
- **Security.** `govulncheck` exits 0 in the Go repos; `pip-audit` reports no
  new advisory in the Python services; `gitleaks` clean.
- **No unpinned tooling.** This stack has been broken three separate times by
  `@latest`/unpinned tools raising their own requirements: ruff 0.16.0 held
  norse-stack red for seven weeks, and x/vuln v1.8.0 broke huginn and would have
  broken sleipnir. Treat an unpinned tool in CI as an unversioned dependency on
  someone else's release schedule.
- **Honesty.** A negative or inconclusive result is a valid deliverable and is
  published as-is. Never report a metric that is hardcoded, degenerate, or not
  actually computed — see the `hitRate` note above.

## Kill criteria

Stop and escalate rather than grinding:

- If P1 shows the bookDepth dumps lack the resolution to reconstruct the live
  `obi` faithfully, **stop**. Report that, and the roadmap ends there rather
  than substituting another proxy and calling it parity.
- If the same check fails three times in a row, revert and escalate with the
  root cause. Do not keep retrying a failing fix.
