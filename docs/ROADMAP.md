# ROADMAP

Two tracks run in parallel.

- **Track A — Book-true replay parity.** The novel goal: make the project's
  headline claim true at the signal level. Phases P1 to P5 below.
- **Track B — Operational health.** The unglamorous work that keeps Track A
  honest. A parity test proves nothing if CI is red or the linter is unpinned.

Track B has priority when it is red. A green build is a precondition for
trusting any result Track A produces, not a separate concern.

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
- GitHub Actions scopes caches **by ref**. A branch can read `main`'s caches;
  `main` cannot read a branch's. Proving a cache-dependent workflow on a feature
  branch therefore does NOT carry the cache over on merge, and `main` must seed
  its own. This cost a wrong claim once already.
- muninn's NVD cache now converges on `main`: a cold run was cancelled at 44 min
  but still banked 99MB, and the next run restored it and finished in **13.5
  min**, failing on `failBuildOnCVSS=7` rather than being cancelled. Fast-and-red
  is the success signature there; slow-and-cancelled is the failure signature.
- One unexplained observation, left open deliberately: that cold run was
  cancelled at 44 min with `The operation was canceled` and no timeout message,
  against a 90-min step and 120-min job budget. Neither limit was reached and no
  competing run cancelled it. It has not recurred since the cache restores. If a
  cold run stalls near 44 min again, look at runner-level limits rather than
  raising timeouts.

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

---

# Track B — Operational health

Track A produces measurements. Track B is what makes those measurements
trustworthy. It is sequenced first whenever it is red.

## B1 — muninn CVE backlog 🔴

muninn's `main` is red on its Security workflow, and correctly so. The
dependency-check scan was down for a month (a cache deadlock, since fixed) and
on its first working run reported a substantial backlog.

The shape of the problem matters more than the count: several advisories are in
**shaded** artifacts — `jackson-databind` 2.21.3 inside `parquet-jackson`
1.17.1, `hive-storage-api` 2.8.1 inside `orc-core` 1.9.8 — where a
`dependencyManagement` pin does not reach the shaded copy. Those need either an
upgrade of the parent artifact or a dated, reasoned, scoped suppression.

**Not acceptable as a fix:** relaxing `-DfailBuildOnCVSS=7`. That gate is
deliberate and the workflow says so.

**Exit criteria.** The scan passes, or every remaining advisory has a written,
dated justification for why it is suppressed or unreachable.

## B2 — Unpinned tooling sweep 🟡

This stack has been broken three separate times by tools that raised their own
requirements without warning:

| tool | what happened | blast radius |
|---|---|---|
| `ruff` (unpinned) | 0.16.0 broadened its default rule set | norse-stack red 7 weeks, ~48 nightly runs |
| `govulncheck@latest` | x/vuln 1.8.0 required Go >= 1.26 vs pinned Go 1.25 | huginn CI red, and it MASKED a real CVE fix |
| `govulncheck@latest` | same | sleipnir, caught pre-emptively before it broke |

Remaining known-unpinned and worth closing: `pip install pytest pytest-cov` in
norse-stack CI (blocking), `pip install pip-audit` in norse-stack
(continue-on-error, harmless) and in muninn-py (blocking — though for a scanner,
surfacing new advisories is the point; the real risk is the tool raising its
Python floor the way x/vuln raised its Go floor).

**Exit criteria.** No unpinned tool gates a build. Each pin has a dependabot
entry so it cannot rot into permanent staleness.

## B3 — Scheduled-task hygiene 🟡

Two existing scheduled tasks are not doing their jobs:

- `nightly-roadmap-advance` runs twice daily and bails every time on a
  "no open PRs" gate that dependabot refills continuously. It cannot self-clear.
  Either exempt `dependabot/*` or retire it. It also pushes to `main` directly,
  which is not an autonomy profile worth extending.
- `merge-dependabot-prs` runs nightly yet left huginn with six open PRs, one
  carrying a fix for a reachable CVE untouched since 2026-09-03.

**Exit criteria.** Every scheduled task either does useful work or is retired.
A task that reliably produces nothing is worse than no task, because it
manufactures the appearance of coverage.

---

# Operating model

## Cadence

`norse-parity-advance` runs weekly (Tuesdays), advances **one** phase per run,
and exits silently when nothing is actionable. A quiet run is a correct outcome.
It may merge its own PR, but only on a fully green CI gate — never on a local
pass alone.

It deliberately **ignores `dependabot/*` PRs** in its in-flight check. Requiring
zero open PRs is what deadlocked `nightly-roadmap-advance` for months.

## Parallelism

Phases within a track are sequential — P2 cannot start before P1 answers whether
book depth is even reconstructable. Tracks A and B are independent and run in
parallel, in different repos, to avoid collisions.

## Definition of done

A phase is done when its exit criteria are met AND the quality gate passes on
CI, not locally. "It builds" is not done. "The tests I wrote pass" is not done
if the suite is red elsewhere.

## What counts as success

A **negative result, honestly reported, is a successful outcome.** If P1
concludes that bookDepth dumps cannot reconstruct the live signal, the roadmap
ends at P1 and that finding is published. The goal is a trustworthy answer, not
a flattering one. This is the same discipline `docs/EDGE_VERDICT.md` already
applies to the strategy itself.
