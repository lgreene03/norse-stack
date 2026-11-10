# Iteration goals in detail (2026-11-04 to 2026-12-15)

This is the working register for the iteration set out in
[NEXT_ITERATION.md](NEXT_ITERATION.md). That document says why and what; this one
says who, by when, and how anyone can tell a goal is done. It is assembled from the
deliverables the five specialist roles committed to in their reviews on 2026-11-04
(definitions in [.claude/agents](../.claude/agents)), so the owners and dates are
theirs.

Every goal has an owning role, a due date, the goals it depends on, and a "done
when" test that a person or agent can check without trusting the owner's word: a
command, a test in CI, or an artefact at a stated path. A goal is not done until its
test passes on main. Status is updated in the same pull request that changes it.
The weekly review is held on Tuesdays (11-10, 11-17, 11-24, 12-01, 12-08 and
12-15); it checks every goal due in the week before it.

## Summary

| ID | Goal | Owner | Due | Status |
|---|---|---|---|---|
| N1 | Pre-registered verdict published | Quant | 12-15 | Not started |
| P1 | Every verdict reproduces independently within five working days | CTPO | 12-15 | Not started |
| P2 | Zero unledgered trials | Security | Continuous | Holding |
| P3 | Recorder completeness at least 99.5% of minutes per venue | Technical | Continuous | Not yet measured |
| G0.1 | Stack ports bound to loopback, CI guard | Security | 11-04 | Done |
| G0.2 | Exposure follow-ups closed | Security | 11-07 | Not started |
| G0.3 | Optional host hardening beyond Norse | Owner | Owner's choice | Not started |
| G0.4 | Credentials passed by file, never on a command line | Security | 11-10 | Not started |
| G0.5 | Owner decisions memo and decisions log | CTPO | 11-06 | Done 11-10 ([DECISIONS.md](DECISIONS.md)) |
| G0.6 | Data coverage report | Quant | 11-07 | Not started |
| G0.7 | Pre-registration signed and tagged `prereg-v1` | Quant | 11-07 | Not started |
| G0.8 | Cause of huginn's idleness confirmed and instrumented | Quant and technical | 11-10 | Not started |
| G0.9 | Risk manager on event time; fold 4 explained | Technical | 11-10 | Not started |
| G0.10 | muninn features under their own prefix | Technical | 11-10 | Done 11-10 ([muninn#87](https://github.com/lgreene03/muninn/pull/87)) |
| G0.11 | Documentation stops overclaiming | CTPO | 11-10 | Not started |
| G0.12 | Console honesty and accessibility patch | Interface | 11-07 | Not started |
| G0.13 | Grafana repaired, Recording Health added | Interface | 11-10 | Not started |
| G0.14 | Alerts that reach someone | Security and technical | Deferred | Deferred by owner (D2) |
| G0.15 | Daily recording gap audit | Technical | 11-10 | Not started |
| G0.16 | Retire huginn's unused web app | Interface | 11-17 | Done 11-10 ([huginn#40](https://github.com/lgreene03/huginn/pull/40), [#34](https://github.com/lgreene03/norse-stack/pull/34), [muninn#88](https://github.com/lgreene03/muninn/pull/88)) |
| G0.17 | Recordings moved to the external drive | Technical | 11-12 | Done 11-10 ([#33](https://github.com/lgreene03/norse-stack/pull/33)); copy verified byte for byte, old volume held until 11-17 |
| G0.18 | Record ETH, SOL, XRP and DOGE on both venues | Technical | 11-17 | Done 11-10 ([muninn#89](https://github.com/lgreene03/muninn/pull/89), [muninn#90](https://github.com/lgreene03/muninn/pull/90)); five pairs live from 22:10:51Z |
| G1.1 | Dataset loaders with checksums and manifests | Technical | 11-14 | Not started |
| G1.2 | Research gateway locked down | Technical | 11-14 | Not started |
| G1.3 | Gate statistics with known-answer tests | Quant and technical | 11-14 | Not started |
| G1.4 | Threat model for the ledger and the seal | Security | 11-13 | Not started |
| G1.5 | Hash-chained trial ledger | Technical | 11-17 | Not started |
| G1.6 | Holdout sealed in code | Technical | 11-17 | Not started |
| G1.7 | Gate calibration on no-skill and planted-edge data | Quant | 11-17 | Not started |
| G1.8 | Gate status page and console panel | Interface | 11-17 | Not started |
| G1.9 | Target-position harness shared by backtest and paper | Technical | 11-19 | Not started |
| G1.10 | Console v2 designed with Claude Design | Interface, with the owner | 11-24 | Not started |
| G2.1 | Benchmark and control runs | Quant | 11-21 | Not started |
| G2.2 | H1 trend trials | Quant | 11-26 | Not started |
| G2.3 | H2 funding carry trials | Quant | 12-01 | Not started |
| G2.4 | Tear sheet per hypothesis | Interface | 12-01 | Not started |
| G2.5 | Manifest audit and licence register | Security | 12-01 | Not started |
| G2.6 | Container keep, freeze or retire list | CTPO | 11-24 | Not started |
| G2.7 | Ledger audit and freeze | CTPO | 12-01 | Not started |
| G3.1 | Both README outcomes drafted before the unseal | CTPO | 12-08 | Not started |
| G3.2 | The single holdout run | Quant, with the owner | 12-08 | Not started |
| G3.3 | Verdict memo | Quant | 12-10 | Not started |
| G3.4 | Security attestation from a clean clone | Security | 12-12 | Not started |
| G3.5 | Independent reproduction | Technical | 12-12 | Not started |
| G3.6 | Generated verdict page | Interface | 12-15 | Not started |
| G3.7 | Verdict published | CTPO | 12-15 | Not started |

## The outcome and its guardrails

**N1. Pre-registered verdict published.** Owner: quant. Due 12-15. Depends on every
M0 to M3 goal. Done when EDGE_VERDICT.md records, for H1 and H2, pass or fail against
the criteria fixed in `prereg-v1`, each figure citing a ledger entry hash, a dataset
manifest hash and a commit SHA, and the matching README outcome is merged.

**P1. Independent reproduction.** Owner: CTPO. Due 12-15. Depends on G3.5. Done when
an agent with no access to this iteration's conversations, starting from a clean clone
and the published instructions, regenerates every number in the verdict exactly,
within five working days of the hypothesis being registered. This is the product's
measure of viability: a registered idea becomes a trustworthy answer cheaply.

**P2. Zero unledgered trials.** Owner: security. Continuous from 11-07. Done when the
ledger audit (G2.7) finds no out-of-sample result on any branch, notebook, log or
page that lacks a ledger entry. Until the ledger exists (G1.5) no out-of-sample
result for H1 or H2 may be produced at all.

**P3. Recorder completeness.** Owner: technical. Continuous, measured from 11-10.
Depends on G0.15. Done when the daily gap audit shows at least 99.5% of UTC minutes
with at least one trade and one book event per venue, over the iteration. Below that,
the CTPO is told the same day.

## M0, by 2026-11-10: safe, honest, pre-registered

The point of M0 is that nothing built later can be undermined by an exposed service,
a leaked credential, a misleading screen or a criterion chosen after the fact.

**G0.1. Stack ports bound to loopback.** Owner: security. Done 2026-11-04 in
[#28](https://github.com/lgreene03/norse-stack/pull/28): 22 of 22 published ports bind
to 127.0.0.1 (20 of 24 were exposed before), Postgres has no host ports, `.env`,
`.env.*` and `secrets/` are ignored, and the validate job fails on any port not bound
to loopback. Verified on the running stack after recreation.

**G0.2. Exposure follow-ups closed.** Owner: security designs, technical implements.
Due 11-07. Depends on G0.1. Done when `console/serve.py` binds 127.0.0.1; the CI guard
also covers services in inactive profiles (`--profile '*'`), rejects `network_mode:
host` and environment-substituted bind addresses, and checks any committed compose
override; `.gitignore` also covers `*.pem`, `*.key`, `.envrc` and
`docker-compose.override.yml`; and huginn's and muninn's own compose files no longer
publish Postgres on every interface. Each is shown failing on a deliberately bad
fixture before passing.

**G0.3. Optional host hardening beyond Norse.** Owner: the owner. No deadline. Norse
does not depend on it. Done when the other projects' containers that still bind every
interface (the audit-service container on 8080, and the gravix containers on 8090 and
8091) bind 127.0.0.1, and Screen Sharing or Remote Management (port 3283) is off if
unused. If the macOS firewall is turned on, untick automatic acceptance for signed
software, or it will let Docker Desktop through regardless.

**G0.4. Credentials by file.** Owner: security. Due 11-10. Depends on G0.1. Done when
`scripts/research/fetch-recorded-books.sh` passes object-store credentials through an
env file rather than `-e` on the command line, ideally for a read-only user scoped to
`muninn-raw`; the Alertmanager receiver reads its secret through a `*_file` field fed
from an untracked file under `secrets/`; and `gitleaks` passes on the repository.

**G0.5. Owner decisions memo and log.** Owner: CTPO. Due 11-06. Done when
`docs/DECISIONS.md` lists each open decision with its recommendation and rationale:
the owner of `features.obi.v1`, the alert channel, repointing the weekly scheduled task,
paper short positions on perpetuals for H2, the holdout key holder and who has seen
2026 prices, recording ETH, SOL, XRP and DOGE, and retiring huginn's unused web app.
Each entry is then marked decided, with the date and the owner's answer, and the goals
that depend on it are unblocked or re-planned in the same pull request.

**G0.6. Data coverage report.** Owner: quant. Due 11-07. Done when
`docs/research/data-coverage.md` gives, for each candidate instrument and data type
(spot daily and four-hour klines, perpetual funding and premium index), the first and
last available date on data.binance.vision, the gaps found, and how many years each
hypothesis can be developed on. It fixes the trial caps that history can support, by
the minimum backtest length rule.

**G0.7. Pre-registration signed.** Owner: quant; the CTPO approves. Due 11-07. Depends
on G0.5 (the H2 decision) and G0.6. Done when `docs/PREREGISTRATION.md` states, for H1
and H2: the dated universe rule, signal, sizing, rebalance and fill rules; every grid
value to be tried; the trial split (12, 12 and 6 in reserve, or the caps G0.6 allows);
costs of 10 bps a side plus the executor's slippage; the benchmark and the control
runs; every pass criterion; and a table of the net Sharpe each criterion implies at
N = 10, 20 and 30. It is tagged `prereg-v1` and pushed. A change before the first
trial needs a new tag with its reason recorded; after the first trial, none is
allowed.

**G0.8. Why huginn does not trade.** Owner: quant, with the technical specialist.
Due 11-10. Done when huginn exports a counter of blocked entries by reason and the time
of its last order intent; the cause of the silence since 2026-06-22 is stated in
EDGE_VERDICT.md with that evidence (the leading suspect is the one-position-per-
instrument check with five positions open and no exit path); and the console shows it.

**G0.9. Risk manager on event time.** Owner: technical. Due 11-10. Done when a test
that reproduces fold 4's -10,000 cap fails before the change and passes after it; the
daily loss window rolls on event time in research and on the wall clock live;
recent-price history is kept per instrument; and the six-month verdict is re-run, with
the corrected fold 4 recorded in EDGE_VERDICT.md. This re-run belongs to the retired
strategy, not to H1 or H2, so it does not draw on their budget.

**G0.10. muninn features under their own prefix.** Owner: technical. Due 11-10.
Depends on G0.5. Done when muninn publishes features under `muninn.features.`; its
shadow comparator, live stream and archival subscriptions follow; a test fails if any
producer writes to an unprefixed `features.` topic; `mvn verify` is green; and
norse-stack documents that muninn's feature engine may now be enabled without touching
huginn's input.

**G0.11. Documentation stops overclaiming.** Owner: CTPO. Due 11-10. Done when the
README, ROADMAP and EDGE_VERDICT no longer present order-book imbalance as an alpha or
book-true parity as achieved; EDGE_VERDICT carries the decile test; and a grep for the
old claims finds nothing.

**G0.12. Console honesty and accessibility.** Owner: interface. Due 11-07. Done when
the Alpha Factory labels order-book imbalance "retired, entry timing only" and hides IC
until it is computed per instrument; the static verdict banner is removed or generated;
"today" figures are either computed for today or relabelled as lifetime; fills show
their dates and marks their age; an IDLE state replaces a green RUNNING when nothing
has happened; the RUN button is gone until the gateway is locked (G1.2); a grep finds
no baked-in result numbers; text is at least 12 px with contrast of at least 4.5:1;
and the console's lint and smoke checks pass.

**G0.13. Grafana repaired, Recording Health added.** Owner: interface. Due 11-10.
Depends on G0.15 for the gap metric. Done when every panel's query names a metric that
exists (for example `huginn_risk_halt_active`), no panel shows "No data" for a counter
that should read 0, and a Recording Health row shows per venue the ingest rate,
reconnects per day, minutes with no events, recorder flush age and flush failures.
`scripts/validate-monitoring.py` passes.

**G0.14. Alerts that reach someone.** Deferred by the owner on 2026-11-10 (decision D2:
the stack is not production). Revisit before any live paper wiring. As originally
specified: owner: security for the receiver, technical for the rules. Depends on G0.4 and G0.5. Done when rules exist, each with a
promtool unit test, for a venue silent for five minutes, recorder flush failures,
object-store write errors, free disk below 15%, and an always-firing watchdog;
`NoFillsExecuted` either fires correctly or is removed; the receiver chosen by the
owner is wired through a credential file; and one test alert is seen arriving.

**G0.15. Daily recording gap audit.** Owner: technical. Due 11-10. Done when a
scheduled job writes, per venue and day, the minutes covered, the gaps longer than 60
seconds and the bytes recorded, to a file and a metric that P3 and G0.13 read.

**G0.16. Retire huginn's unused web app.** Owner: interface; technical reviews. Due
11-17. Decision D6. Done when `huginn/web` and its CI job are removed, no document or
compose file refers to it, nothing else in huginn depended on it (shown by huginn's full
CI passing), and its mock-fill route into the real executor is gone with it.

**G0.17. Recordings moved to the external drive.** Owner: technical; security reviews.
Due 11-12. Decision D5. Done when MinIO's data is a named bind mount whose host path
comes from `MINIO_DATA_DIR` (default unchanged for other machines), set locally in the
git-ignored `.env` to a folder on "Mac 4T" alongside `MUNINN_HOST_PORT=18080`; the
existing recordings were copied, not moved, and verified by object count and total size
before the switch; the recorder resumed with no gap beyond Kafka's retention; and the
old anonymous volume is kept untouched for a week before anyone removes it. The
behaviour when the drive is absent (MinIO fails, the recorder pauses with backoff and
catches up from Kafka) is documented.

**G0.18. Record ETH, SOL, XRP and DOGE on both venues.** Owner: technical; quant
reviews. Due 11-17. Depends on G0.17. Done when muninn's Binance adapter subscribes to
every configured instrument and attributes each message to its own instrument from the
stream name (today it uses only the first), with a test that interleaved messages for
two instruments are never mixed; OKX is configured for the same five pairs; the raw
recorder writes each pair under its own instrument partition; and the gap audit (G0.15)
covers all ten venue-pair streams.

## M1, by 2026-11-19: a gate that cannot be fooled

M1 builds the instrument before anyone looks through it. Nothing in M2 starts until
G1.7 has passed.

**G1.1. Dataset loaders and manifests.** Owner: technical. Due 11-14. Depends on G0.6.
Done when daily and four-hour klines, 15-minute klines for fill prices, funding and
premium index load from data.binance.vision for every instrument the dated universe
can select, delisted ones included; each file is verified against its published
`.CHECKSUM` and capped in size; a missing file or a gap fails the load instead of
being skipped; development and holdout data have separate committed manifests (file,
SHA-256, rows, gaps, licence); and a test shows closed klines from the public REST API
equal the archive rows.

**G1.2. Research gateway locked down.** Owner: technical; security reviews. Due
11-14. Done when the gateway requires a token, sends CORS only to the console's own
origin, accepts only datasets named by manifest hash, and refuses a run whose ledger
entry cannot be written. Until then its run endpoint is disabled.

**G1.3. Gate statistics.** Owner: quant specifies, technical implements. Due 11-14.
Done when each statistic has a known-answer test from a published worked example and a
synthetic check: combinatorially symmetric PBO over a T by N daily-return matrix in 16
blocks (12,870 splits); the deflated Sharpe on joined daily out-of-sample returns with
skew, kurtosis and N read from the ledger; minimum track record length; 20-day block
bootstrap intervals; leave-one-year-out and minus-best-20-days robustness; and the
volatility-matched equal-weight buy-and-hold benchmark. huginn's fold-level PBO proxy
and per-fold averaged deflated Sharpe are removed or renamed so they cannot be
mistaken for these.

**G1.4. Threat model for the ledger and the seal.** Owner: security. Due 11-13. Done
when `docs/research/ledger-threat-model.md` lists how a result could be quietly re-run,
edited, deleted, reordered, or computed on unsealed or unmanifested data, and names the
adversarial test that proves each is refused.

**G1.5. Hash-chained trial ledger.** Owner: technical; security designs and reviews.
Due 11-17. Depends on G1.4. Done when every run appends to an append-only JSONL ledger
in git an entry holding the previous entry's hash, the configuration hash, the commit
SHA, the dataset manifest hash, the cost basis, a summary of the result and a
timestamp; the harness refuses to run on a dirty tree, on unmanifested data, or when
the write fails; N in every statistic is read from the ledger; a tag marks the ledger
head at each milestone; and the adversarial tests from G1.4 (an edited, deleted or
reordered entry, a dirty tree, unmanifested data, an unauthenticated submission) all
pass in CI.

**G1.6. Holdout sealed in code.** Owner: technical; security reviews. Due 11-17.
Depends on G1.5. Done when the harness refuses any event on or after 2026-01-01 unless
an unseal names the `prereg-v1` hash and presents the owner's key; the unseal is
written to the ledger; a second unseal fails; and tests prove an early unseal, a repeat
unseal and an unseal without the key are each refused.

**G1.7. Gate calibration.** Owner: quant. Due 11-17. Depends on G1.3 and G1.5. Done
when `docs/research/gate-calibration.md` shows the full gate passing at most 5% of
1,000 simulated no-skill ledgers at N = 30, detecting a planted Sharpe of 1.5, and
reporting its power at Sharpe 1.0, 1.5 and 2.0. If calibration fails, no strategy is
judged, and that is itself an outcome to report.

**G1.8. Gate status page and console panel.** Owner: interface. Due 11-17, with the
result schema agreed with the technical specialist by 11-12. Done when a generated
`GATE_STATUS` page and a console panel show, per hypothesis, PENDING, FAIL or PASS in
words; trials used against the budget; the ledger head hash; the deflated Sharpe and
PBO; and the holdout as SEALED or OPENED with the time. Nothing on it is typed by hand.

**G1.9. Target-position harness.** Owner: technical. Due 11-19. Depends on G0.9.
Done when a strategy can hold volatility-targeted weights across several instruments,
with per-instrument positions, exits and a rebalance band, at 10 bps a side plus
slippage (the 14 bps and 5 plus 2 bps defaults are aligned); the same strategy object
runs the backtest and paper; and a golden-fixture test shows both emit identical
orders for the same inputs.

**G1.10. Console v2 designed.** Owner: interface, with the owner present for the
design sessions. Due 11-24. Decision D6. Done when a Claude Design prototype covers the
gate status, a tear sheet, the verdict page and recording health, laid out for the data
the gate actually produces (G1.8's schema), meeting the iteration's interface and
accessibility rules, and the owner has approved it. Building it is folded into G2.4 and
G3.6, which then render inside the new console instead of as separate pages; the
existing console's trading panels are removed once the new one covers what they showed.

## M2, by 2026-12-01: the trials

**G2.1. Benchmark and control runs.** Owner: quant. Due 11-21. Depends on M1. Done
when buy-and-hold and random-sign control runs are in the ledger, marked
unselectable, with their statistics on the gate status page.

**G2.2. H1 trend trials.** Owner: quant. Due 11-26. Depends on G0.7 and M1. Done when
at most 12 H1 trials (or the cap G0.6 sets) are in the ledger, exactly as
pre-registered, with no configuration outside the registered grid.

**G2.3. H2 funding carry trials.** Owner: quant. Due 12-01. Depends on G0.5 (paper
shorts approved, or H2 reverted to a filter on H1) and M1. Done when at most 12 H2
trials are in the ledger, with funding accrued every eight hours and both legs
costed.

**G2.4. Tear sheets.** Owner: interface. Due 12-01. Depends on G2.2 and G2.3. Done
when each hypothesis has a generated tear sheet: returns net of 20 bps with intervals,
the deflated Sharpe, PBO, minimum track record length, the benchmark comparison and
cost sensitivity at 15, 20 and 30 bps a round trip, with the trial count and data
window beside every figure.

**G2.5. Manifest audit and licence register.** Owner: security. Due 12-01, with the
manifest audit by 11-24. Done when every dataset file is re-verified against its
manifest with the gaps listed, and `DATA_LICENCES.md` records the licence and terms of
each source (Binance public data under CC BY-NC-SA 4.0, the live API terms for the
recordings) and confirms nothing restricted is committed.

**G2.6. Container keep, freeze or retire list.** Owner: CTPO. Due 11-24. Done when
each running container has a written decision with its cost in memory and attention,
and anything retired is removed in a pull request.

**G2.7. Ledger audit and freeze.** Owner: CTPO, with security. Due 12-01. Done when
the chain verifies, the trial count is within budget, no holdout data has been read,
P2 holds, and the frozen ledger head is tagged.

## M3, by 2026-12-15: the verdict

**G3.1. Both outcomes drafted first.** Owner: CTPO. Due 12-08, before the unseal. Done
when the README text for a pass and for a fail are both written and reviewed, so the
result cannot shape the story told about it.

**G3.2. The single holdout run.** Owner: quant, with the owner holding the key. Due
12-08. Depends on G2.7 and G3.1. Done when exactly one candidate (or none, if none
passed development) is run once on the holdout, and the unseal and the result are in
the ledger.

**G3.3. Verdict memo.** Owner: quant. Due 12-10. Done when the memo states pass or fail
per criterion, what the tests could and could not detect in numbers, and, on a pass,
the calibrated edge model that will replace huginn's fixed 60 bps per unit.

**G3.4. Security attestation.** Owner: security. Due 12-12. Done when one script, run
from a clean clone, verifies the ledger chain, the manifests and exactly one unseal,
and its output is committed.

**G3.5. Independent reproduction.** Owner: technical. Due 12-12. Depends on G3.3. Done
when a fresh agent with no prior context reproduces the verdict's numbers exactly from
a clean clone using only the repository's instructions, which is the test of P1.

**G3.6. Generated verdict page.** Owner: interface. Due 12-15. Done when the verdict
page is generated from the ledger and results; on a fail the trading panels are
removed or reframed around recording and research; on a pass a weekly
paper-versus-backtest shortfall template is ready for the next iteration.

**G3.7. Verdict published.** Owner: CTPO. Due 12-15. Done when EDGE_VERDICT.md, the
matching README outcome and this register's final statuses are merged, and the next
iteration's goal is drafted: paper wiring on a pass, the research-platform positioning
on a fail.

## Follow-ups from reviews

Findings that independent reviews raised against merged work and did not block it.
They are owned and visible here rather than lost in pull request threads; the driver
picks them up after the dated goals, highest severity first, and the CTPO may promote
one to a goal.

| Severity | Finding | Raised by, on | Owner |
|---|---|---|---|
| Medium | Redpanda has no authentication or ACLs, so any client on the compose network can still write to `features.obi.v1`, huginn's trading input. Allow only obi-bridge to write it. | Security, muninn#87 | Security |
| Medium | muninn's feature archival has no on/off switch, labels every batch `instrument=BTC-USDT` whatever the symbol, and can lose up to a batch on failure. Fix before enabling the engine for any other symbol. | Security, muninn#87 | Technical |
| Medium | huginn's Helm chart points its HTTP Service port at 8084, where nothing listens; HTTP and metrics share 8081. Needs a template change, since two ports of 8081 would be invalid. | Technical, huginn#40 | Technical |
| Medium | `TestProp_FlipThroughZero` in huginn's portfolio package is flaky: a 37.21 fee on a 0.029 short drove `AverageCost` negative. Either the generator is unrealistic or the ledger mishandles fees through zero. | Technical, huginn#40 | Quant |
| Medium | The console server (`console/serve.py`) binds every interface and proxies to huginn's breaker and the research gateway. Already within G0.2. | Security, #28 | Security |
| Low | `FeatureParquetWriter.buildPartitionPath` puts an unchecked `featureName` from the record into the storage path. | Security, muninn#87 | Technical |
| Low | muninn's topic-namespace tests watch one form of `send`; add `verifyNoMoreInteractions`, and refuse `features.` with a hard-coded check rather than only the shared prefix constant. | Security, muninn#87 | Technical |
| Low | `muninn.streaming.topic-pattern`, `muninn.stream.topics` and the consumer group names are unvalidated configuration that could point muninn back at `features.*` or into huginn's consumer group. | Security, muninn#87 | Technical |
| Low | muninn's live stream pattern also delivers `.replay` and `.DLT` records to clients as if they were live. | Technical and security, muninn#87 | Technical |
| Low | huginn's `docs/OPERATIONS.md` and `docs/architecture.md` say an empty `HUGINN_API_TOKEN` disables auth; the code refuses with 503. `/api/stream`, `/api/snapshot/history` and the auth in `PUT /api/strategy/config` have no tests. | Technical, huginn#40 | Technical |
| Low | Stale topic names in norse-stack's `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md` and ADR-0003 (for example `features.vwap.1m.v1` listed with huginn as consumer), and huginn docs saying muninn writes `features.*`. | Technical, muninn#87 | Technical |
| Medium | Gap detection cannot see outages: sequence numbers restart at 1 on every muninn restart, messages missed while reconnecting leave no gap, and Binance's `lastUpdateId` is discarded. Record a connection count and `lastUpdateId` (an event schema change). | Quant and technical, muninn#89 and #90 | Technical |
| Medium | `events.book.snapshot` was never provisioned, so it runs on broker defaults: 7 days and no size cap, about 55 GB on the broker with five pairs. Trades keep only 24 hours, so a longer outage leaves a silent gap. | Technical, #33 | Technical |
| Medium | Nothing alerts when the recorder is holding or MinIO is down; with folder creation off, a missing drive becomes a silent hold that loses trades after 24 hours. Alerts are deferred (D2), so the gap audit (G0.15) must at least show it. | Security, #33 | Security |
| Medium | Redpanda's data is an anonymous volume and `make down` (and `smoke.sh --teardown`) run `down -v`, which deletes it. MinIO's live store still uses the default credentials; rotate them with a migration. | Technical and security, #33 | Security |
| Low | muninn's OKX adapter has no adapter-level reconnect test, and files any spot instrument the socket sends whether subscribed or not (Binance refuses unsubscribed streams). The connected-socket assignment has no generation check after a fast stop and start. | Technical and quant, muninn#90 | Technical |
| Low | verify-minio-copy.sh accepts the same folder spelt two ways, requires `muninn-raw` to exist but not to hold objects, and does not compare empty directories. Runbook step 8 overstates that `--mount` never creates a source. | Security, #33 | Technical |

**Data boundaries.** From 2026-11-10T22:10:51Z muninn records five pairs on both
venues. From the same time a refused OKX book consumes a sequence number, and OKX
sequences are counted per instrument and event type; Binance BTC numbering and the
content of every BTC event are unchanged. Recordings before 2026-11-10T22:04Z were
copied byte for byte from the old volume to the external drive (77,326 files,
2,791,677,446 bytes). The old volume (`ee56a161`, held by the stopped container
`minio-old-data-keep`) may be removed from 2026-11-17 by running the runbook's last
step.

## Capacity and risk

The technical specialist estimates about 21 engineer-days of work against 29 working
days, so the plan holds with little slack. If it slips, the CTPO cuts in this order:
G2.6, then G0.15's metric (keeping its file), then G2.3, which would leave H1 alone in
the verdict. The verdict date does not move; a smaller verdict on time beats a larger
one late.

Status on 2026-11-10: M0 has slipped. The scheduled driver ran six times from
2026-11-04 but merged nothing, because each run stopped at a permission prompt and
timed out; the owner's decisions were taken on 11-10 instead of 11-06. The M0 goals
still open carry their original dates in the table so the slip stays visible; the
CTPO's review of 11-10 re-plans them.
