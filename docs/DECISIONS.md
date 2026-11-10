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
