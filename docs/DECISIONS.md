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

The five decisions below were open questions in the pre-registration (G0.7,
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
option could be chosen blind; the categories follow the hypothesis's definition. Before
signing, the CTPO checks the ten other holdout-only candidates against the categories from
their own published descriptions.

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
chosen deliberately.
