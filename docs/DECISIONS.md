# Owner decisions

Decisions that belong to the owner, recorded once with the recommendation they answered
and the reason, so no agent has to guess and none is ever taken on the owner's behalf.
Goal IDs refer to [ITERATION_GOALS.md](ITERATION_GOALS.md). Newest at the bottom.

## 2026-11-10

**D1. Who owns `features.obi.v1`.** Decided: obi-bridge keeps it, frozen; muninn
publishes its features under `muninn.features.`. huginn already reads obi-bridge's
format, muninn's own order-book imbalance is retired as an alpha, and the prefix is the
smallest change: muninn's producer and its own subscriptions, plus a test that forbids
writing to an unprefixed `features.` topic. It also lets muninn's feature engine run in
the stack without any route into huginn's trading input. If a muninn feature ever earns
a place in trading, huginn is migrated to it deliberately then. Unblocks G0.10.

**D2. Where alerts go.** Decided: nowhere yet. The stack is not production, so no
receiver is wired. G0.14 is deferred and must be revisited before anything moves to
live paper wiring. The daily recording gap audit (G0.15) still goes ahead, because
guardrail P3 has to be measured whether or not anyone is paged.

**D3. Paper short positions on perpetuals for H2.** Decided: yes, for research and
paper only. Without the short leg, funding carry is only market exposure. Recorded
caveat: this is not a live path for the owner as things stand, because crypto
derivatives cannot be sold to UK retail customers and Binance's public-data terms
(clause 4.2) prohibit live proprietary trading on that data, so a pass on H2 alone
would not make the product tradeable. Unblocks G2.3.

**D4. Who holds the holdout key.** Decided: the owner, and only the owner; never an
agent or a scheduled task. The holdout data is public, so the seal cannot be secrecy:
it is tamper evidence. Every result must cite a dataset manifest hash, so any run on
the holdout without an unseal entry in the trial ledger is detectable. The key proves
that the one permitted unseal was the owner's. In practice it is a passphrase the owner
keeps in a password manager; only a salted hash of it is committed, when G1.6 is built.
The owner types it once, at the single holdout run on 2026-12-08 (G3.2). On who has
seen 2026 prices: realistically the owner and every model involved, which is why the
holdout can only catch a failure, as NEXT_ITERATION.md already states.

**D5. Recording more coins.** Decided: yes, all of ETH, SOL, XRP and DOGE on both
venues, on the external drive. The internal disk is 80% used with 87 GB free; the
external drive ("Mac 4T", APFS on a PCIe SSD) has about 3.5 TB free. At roughly 1 GB a
day for five pairs on two venues that is many years of headroom. Moving the recordings
also fixes a fragility found on the way: they currently live in an anonymous Docker
volume, which a careless recreate could replace with an empty one. Binance recording of
more than one pair needs a muninn change, because its adapter only reads the first
configured instrument; OKX already handles several. New goals G0.17 and G0.18.

**D6. huginn's unused web app.** Decided: retire it now (`huginn/web`), and design one
modern console instead. The old app is not deployed, hardcodes "Uptime: 100%", claims
the engine is executing strategy signals and posts mock fills through the real
executor. The replacement is a single Norse console, designed with Claude Design while
the gate is being built, and built around data that exists once the gate runs: the gate
status, tear sheets, verdict and recording health. Building it earlier would only
display numbers that do not exist yet. New goals G0.16 and G1.10.

**D7. The weekly task `norse-parity-advance`.** No decision needed: the task no longer
exists. The scheduled task `norse-iteration-driver` replaces it.

## 2026-11-11

**D8. A data heartbeat, as a narrow exception to D2.** Decided by the owner on
2026-11-11: notify the owner when recorded data stops arriving, without building full
alerting. Built as a launchd agent on this Mac (`scripts/data-heartbeat.sh`, label
`io.github.lgreene03.norse.data-heartbeat`) that checks every 15 minutes that the drive
is mounted and that each of the twenty streams has written in the last 15 minutes, and
shows a macOS notification only when that state changes. Nothing leaves the machine.
Remove it with `make heartbeat-uninstall`. G0.14 stays deferred.

**D9. What to do with huginn's stuck composite strategy.** Decided by the owner on
2026-11-11, on the recommendation below: leave it idle. Its five stored positions are
not cleared and no exits are added; the metrics from G0.8 keep showing why it does not
trade. G0.8 found that the live composite opened one long in each of its five instruments on
2026-06-22 and has no exit path, so its one-position-per-instrument check refuses every
entry since. The options are to leave it idle, to clear its stored positions (it
trades again and sticks again the same way), or to give it exits. Recommendation:
leave it idle. It trades the retired order-book signal, so un-sticking it only produces
more paper results from a strategy already judged to have no edge, and G1.9's
target-position harness, with explicit exits, is what will run H1 and H2.

**D10. A non-crypto asset class after the verdict.** Open; for the owner after
2026-12-15. Research on 2026-11-11 found no reason to leave crypto before the verdict:
it is the only market with free, keyless, full-depth history, and H1 and H2 run on it.
It also found that H2 cannot be traded by a UK retail client (the FCA ban on crypto
derivatives for retail remains, though crypto ETNs were opened to retail on 2025-10-08)
and that funding carry was attractive on only about 8% of days in 2025. The cheapest
path to something the owner could trade is a daily cross-asset trend study on futures
or UCITS ETFs, the best-documented edge at that horizon (net Sharpe about 0.76 over a
century, mixed since 2020). Its data costs about $270 a year (Norgate futures) and
needs an account, so it would mean relaxing the no-paid-feeds rule for that one
source. Live intraday or order-book equities data is not recommended: it is paid, and
the retired order-book signal showed that short-horizon edges do not survive retail
costs. Free daily FX reference rates can be added at any time.

The five decisions D11 to D15 were open questions in the pre-registration (G0.7,
[PREREGISTRATION.md](PREREGISTRATION.md)). The owner answered "apply recommendations" on
2026-11-11, so each records the quant's recommendation as decided, with the options that
were rejected and why. The holdout key is not among them: D4 already decides it.

**D11. The target Sharpe for the trial caps.** Decided by the owner on 2026-11-11, on the
quant's recommendation: a target Sharpe of 1.0 in the minimum backtest length rule, so
G0.6's caps stand at 12 trials for H1, 12 for H2 and 6 in reserve, 30 in all. The
alternative was 0.75, which would cut the total to 17, the most H2's 5.98 years of
out-of-sample returns support at that target. The deflated Sharpe ratio charges for every
trial actually run whatever the cap, and the grids register only 14 configurations, so the
cap decides only how many reruns after a defect the budget can absorb: 16 at 1.0, 3 at 0.75.
At 0.75 a single harness defect found after the trials (which reruns all 14) would exhaust
the budget and turn a bug into a fail. At 1.0, the best of 30 skill-less trials over H2's
5.98 years is expected to show a Sharpe of 0.85, below every pass mark.

**D12. A position in a pair that halts or is delisted.** Decided by the owner on
2026-11-11, on the quant's recommendation: when a pair's run of daily bars ends (a
collapse such as LUNA's, a suspension such as FTT's, a delisting or a rename), a long
position is closed at the low of its last daily bar (a short at the high), with the usual
fee and slippage on top, and the slot holds cash until the next month end. Rejected:
freezing the position at its last price and exiting when trading resumes, following a
table of successor symbols, because that table is not in the archive and would be typed
from memory with hindsight; writing the position off to zero, because a scheduled rename
such as MATIC to POL is not a total loss; and exiting at the last traded price, the most
optimistic price available in a collapse. The chosen rule never invents a price, since the
low did trade, and errs against the strategy; it is not a worst case, since a coin can end
worth less than its last day's low.

**D13. Pairs younger than H1's 120-day lookback.** Decided by the owner on 2026-11-11, on
the quant's recommendation: a pair joins the universe only once it has a daily close 120
days before the month-end close, and the next eligible pair by volume takes its place,
within the ten largest. This arises at 28 of the 92 development month ends. Rejected:
giving the young pair its slot but holding cash until it has the history, which dilutes
exposure for a reason unrelated to trend; and letting it trade on the lookbacks it has,
which gives new listings a faster signal than every other pair and so mixes two horizons in
one trial. The chosen rule keeps five working slots and a fully defined signal; its cost is
that a new coin enters only after four months.

**D14. Exclusions from H1's universe.** Decided by the owner on 2026-11-11, on the quant's
recommendation: exclusions by category, never by name: fiat and fiat-pegged stablecoins,
leveraged tokens, tokens designed to track an asset outside crypto (commodity-backed
tokens, tokenised shares, funds or other securities), and wrapped, bridged or
liquid-staking forms of another coin. The alternative was to exclude only what development
data shows reaching a top ten, which is stablecoins and leveraged tokens (ETHBEAR 6th and
ETHBULL 10th at the 2020-03 month end). The broader categories change nothing in
development, because no such token reaches a development top ten, so they cannot have been
fitted to development results; they keep the hypothesis about crypto assets in both
periods. Disclosed: G0.6 named three pairs that reach a top ten only in the holdout, PAXG
and XAUT (gold) and SNDKB (a tokenised share), and 2026 gold prices are public, so neither
option could be chosen blind; the categories follow the hypothesis's definition. The CTPO
checked the ten other holdout-only candidates against the categories on 2026-11-13, from
their published descriptions, and found none in an excluded category (the classification
is appendix A of the pre-registration).

**D15. How H2 is compared with buy-and-hold.** Decided by the owner on 2026-11-11, on the
quant's recommendation: H2's second criterion uses its returns in excess of a beta-matched
equal-weight BTC and ETH buy-and-hold (the regression slope removed), while H1 keeps the
volatility-matched comparison NEXT_ITERATION.md names. The comparison exists because a
long-flat trend rule can otherwise pass on market exposure alone; H2 holds equal quantities
long spot and short perpetual, so it has no market exposure to pass on. Applied to H2, the
volatility-matched difference would instead require carry's Sharpe ratio to beat the
market's by a wide margin (about 2.4 to 3.2 under the assumptions in the pre-registration),
testing "better than the market" rather than "not just the market". Rejected: keeping the
volatility-matched comparison for both, for that reason; and beta-matching both, because
for a long-flat trend rule the volatility-matched comparison is the stricter one and was
chosen deliberately. G1.3's `benchmark.py` has only the volatility-matched excess, so the
beta-matched one needs a function with hand-computed known answers before G2.3, and G1.7
must calibrate it.

**D16. Rulesets on the owner's public repositories.** Decided by the owner on 2026-11-11.
The G1.4 threat model (finding 1, threats T10 and T24) found that main had no protection
and tags none either, while one admin identity is shared by the owner and every agent, so
any session could force push an edited ledger or move `prereg-v1`. The owner put rulesets
on the public repositories. On norse-stack, as active on 2026-11-13: main cannot be
deleted or force pushed and needs the `research` and `validate` checks to pass, with no
bypass; tags matching `prereg-*` and `ledger-*` cannot be updated or deleted once created,
with no bypass; and changes to main go through a pull request with one approval, which
repository admins may bypass. The owner is the only person on the project, so the approval
rule guards against outside contributions; the owner's own pull requests, including every
one an agent prepares, merge by that bypass. Creating a tag stays allowed, so the first `prereg-v1` created is permanent; the pre-registration
therefore has the owner create it in person on the merged commit (its section 16). The
rulesets bind agents only while nobody with admin rights lifts them, which the agents'
standing rules forbid.

**D17. The holdout files off disk until the unseal.** Decided by the owner on 2026-11-11:
option B of the G1.4 threat model (finding 4, threat T21). The holdout's 8,517 archive
files leave the local mirror until the single unseal on 2026-12-08, when G1.6 fetches them
again and verifies each against the committed holdout manifest, which is frozen at its
SHA-256. The loader's seal only covers reads through the harness, and the files sat in
plain form on an unencrypted volume that any tool, glob or agent could read; removing
them stops accidental reads, not a deliberate download of public data, which only the
mechanical holdout-candidate rule of the pre-registration makes useless. Implemented for
the tools in [#54](https://github.com/lgreene03/norse-stack/pull/54) (the mirror, the
store, `verify` and the REST check refuse the holdout months); the re-fetch at the unseal
is G1.6's.

## 2026-11-14

**D18. Signing the pre-registration and delegating the merge and tag.** Decided by the
owner on 2026-11-14. The owner signed the pre-registration (G0.7, PR 52) in conversation
("Approved by me") and told an agent session to do what was needed: record the
signature, merge the pull request and create the annotated tag `prereg-v1` on the merged
commit on main. For `prereg-v1` this replaces the rule, in section 16 of the draft and in
D16, that the owner creates the tag in person. The owner's view is that the project is in
development and the ceremony added nothing: one GitHub identity is shared by the owner
and every agent, so neither a signature row nor a tag could prove who acted. What the
freeze must achieve is unchanged, namely that the rules are fixed before the ledger's
genesis entry and before any trial. Any later `prereg-*` tag still needs the owner's
explicit instruction for that tag.

## 2026-11-15

**D19. The `ledger` branch is append-only.** Decided by the owner on 2026-11-15. G1.5
keeps the trial ledger on its own `ledger` branch, which main's rulesets do not cover
because they target the default branch only. The owner approved a ruleset for it, active
since 2026-11-15 as "Ledger branch is append-only" (ruleset 27314544): `refs/heads/ledger`
cannot be deleted or force pushed and must keep a linear history, with no bypass. Appends
are still allowed, so the harness can push each entry; nothing can rewrite one once it is
pushed. Like D16, it binds agents only while nobody with admin rights lifts it.

## 2026-11-16

**D20. No ledger freeze in development.** Decided by the owner on 2026-11-16, on the
round 3 security review of G1.5 ([#56](https://github.com/lgreene03/norse-stack/pull/56)),
whose one Medium finding (N2b) is that a freeze tagged while a run from a second clone is
appending breaks verification for ever. The owner's view is that the project is in
development and needs no freezes, so the finding blocks nothing now and no code is written
for it. The pre-registration still defines N on the frozen ledger (its section 10), so the
one registered freeze at G2.7 stands until a superseding pre-registration says otherwise;
when it is taken, no run starts from any clone other than the one taking it, which closes
N2b by process. Making the freeze a chain entry stays a follow-up in the register.

## 2026-11-21

**D21. No ledger freeze; the unseal closes development.** Decided by the owner on
2026-11-21: "This is local personal dev. No need for freezes." The ledger freeze is
removed entirely, with the `gate.freeze` command, the `ledger-freeze-*` anchor and its
verification, and every rule keyed on it. The unseal entry closes development instead: N,
V and the holdout candidate are computed on the ledger before the unseal entry, and the
ledger, the harness and replay refuse any trial, benchmark or control entry after it. The
pre-registration's "by the freeze on 2026-12-01" goes: a hypothesis is judged only when
every registered configuration has a selectable run, and one that never gets there is NOT
RUN at the verdict, or FAIL if its own entries and the reserve are exhausted.
Cost-sensitivity runs (section 8) are allowed for a hypothesis's candidate once every
registered configuration of that hypothesis has a selectable run, before or after the
unseal, outside N. The holdout seal stays as merged in
[#59](https://github.com/lgreene03/norse-stack/pull/59), apart from item 3 of this change
(the three-run cap counts only runs that reached the unsealed stage, security review round
3, C1) and item 4 (the unseal shows the data loading and seal diff since the candidate's
run and records the hash the owner accepts, round 3, T1). D20's surviving registered
freeze at G2.7 is withdrawn, and with it follow-up N2b, the race between a freeze and a
run from a second clone: the unseal is a chain entry that moves the ledger head, so a run
admitted before it is refused at its append. The owner's instruction covers the
`prereg-v2` tag for this change, as D18 covered `prereg-v1`: the lead creates the
annotated tag on the merged commit on main. At the coordinator's direction `prereg-v2`
also settles what the G3.1 drafting ([#64](https://github.com/lgreene03/norse-stack/pull/64),
its open questions 1, 4, 10 and 11) found D21 leaves open: the unseal waits until each
hypothesis's grid is complete or its own entries and the reserve are exhausted, so no
hypothesis is stopped early by the other's unseal; with no holdout candidate, development
closes at that same point and the verdict records the ledger entry it was computed at; and
the verdict table gains VOID (a broken hash chain) and NOT JUDGED (a gate that did not pass
its calibration). It also discloses huginn's fixture `data/btc_test.jsonl`, which holds
BTC data from 2026-05-18 inside the holdout, as the G2.5 audit
([#63](https://github.com/lgreene03/norse-stack/pull/63)) found. These are the
coordinator's clarifications, not further owner decisions.

## 2026-11-22

**D22. V is the null variance; the calibration's detection clause is restated.** Decided
by the owner on 2026-11-22: "Revise V". G1.7's calibration
([#66](https://github.com/lgreene03/norse-stack/pull/66), docs/research/gate-calibration.md
there) found the gate registered in `prereg-v1` safe but underpowered. No no-skill family
passed more than 1.3% of 1,000 ledgers at N = 30, but a single planted configuration at an
annualised Sharpe ratio of 1.5 passed development in only 144 to 335 of 1,000, so the
detection half of section 10's condition failed. The cause is V, the cross-trial variance
of the selectable runs' Sharpe ratios pooled over both hypotheses with no floor, which a
real edge inflates: SR0 is 0.554 times a single planted edge and 1.065 times an edge shared
within one hypothesis. Section 10 now sets V = 1/(T − 1), the null variance of the
candidate's window, for DEV-1 and DEV-2, with N still from the ledger before the unseal, so
no run's result moves the bar, and restates the detection clause: a single planted
configuration at an annualised Sharpe ratio of 2.0 must pass development in more than 500
of 1,000 ledgers in every single-planted family, with power at 1.5, at N = 14 and for an
edge shared within one hypothesis reported. The old clause asked for 1.5 in every family,
which section 12's own arithmetic ruled out (H2's even-odds mark at N = 30 is 1.52 even
under the null variance). Both changes were chosen after the synthetic calibration and
before any trial or genesis entry, and section 15, item 24 of the pre-registration
discloses that. The quant role's review of #66 recommended the change, with figures from a
reduced check of the revision on normal returns only: false passes in at most 0.14% of
no-skill ledgers, a single 1.5 passing in 46% to 51% of ledgers, a single 2.0 in 88% to 94%
and a shared 1.5 in 35% to 48%. The quant is an AI agent role the owner runs, not an
independent person; its review is advice, not an approval or a signature, and the decision
is the owner's alone. The full calibration rerun under the revised gate, every family at
1,000 ledgers, is G1.7's in #66. It runs after this revision merges to main, so the
detection clause is committed, with its date, before its full results exist, and before
the `prereg-v2` tag, as the option the owner chose said ("The full calibration is rerun
before tagging"); the tag and then the genesis entry follow it. The owner also confirmed,
unchanged, the three clarifications D21 recorded as the coordinator's: the unseal waits
until each hypothesis's grid is complete or its own entries and the reserve are exhausted;
with no holdout candidate, development closes at that same point and the verdict records
the ledger entry it was computed at; and the verdict table's VOID and NOT JUDGED rows. They
are now owner decisions. The change is part of `prereg-v2`
([#65](https://github.com/lgreene03/norse-stack/pull/65)).
