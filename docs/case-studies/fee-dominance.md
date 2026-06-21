# Case study: a real gross edge that fees eat alive

**One line:** the OBI strategy wins ~70% of its round trips with a profit factor
of 5.85 — and still loses money, because it trades ~21x its capital over the
window and the fee/slippage bleed is larger than the edge. This is a write-up of
how that was caught and what the fix direction is.

## The trap: gross looks great, net is negative

On the live Odin analytics path the OBI strategy's *gross* round-trip statistics
are genuinely good: roughly a **70% win rate** and a **profit factor of 5.85**
(gross winners ÷ gross losers). A profit factor near 6 on a 70% hit rate is the
kind of number that, taken alone, gets a strategy promoted to live capital.

The number that stopped that promotion is `net_trading_pnl`, computed in Odin as
`realized_pnl - total_fees`
([`services/odin/odin.py:892`](../../services/odin/odin.py)). Over the observed
window the strategy showed **realized PnL of roughly +4.80 gross but −14.28 net**
after about **$19 of fees across ~23 round trips**. The edge is real; it is just
smaller than the cost of harvesting it. Odin exposes this directly as
`net_trading_pnl` and `fee_drag_pct`
([`odin.py:941`–`942`](../../services/odin/odin.py)) precisely so a gross figure
can never be quoted without its cost-adjusted twin sitting next to it.

## How it was found

Two independent signals converged on the same conclusion:

1. **Live Odin: `net_trading_pnl` vs `realized_pnl`.** Bucketing round trips and
   subtracting fees turned a positive realized figure into a negative net figure.
   The `fee_drag_pct` field made the magnitude legible: fees were not a rounding
   error on this strategy, they were the dominant term. A 70%/5.85 strategy
   bleeding to net-negative is a turnover problem, not a signal problem.

2. **The calibrate grid: PnL gets *less* negative as the threshold rises.**
   Running the parameter sweep
   ([`huginn/cmd/calibrate`](https://github.com/lgreene03/huginn/blob/main/cmd/calibrate/main.go))
   over `threshold ∈ {0.5, 0.6, 0.7, 0.8}` produced a monotone pattern recorded
   in [docs/RESULTS.md](../RESULTS.md):

   | threshold | fills | realized PnL | turnover |
   |----------:|------:|-------------:|---------:|
   | 0.5 | 455 | −235.39 | 41.29x |
   | 0.6 | 338 | −157.71 | 30.65x |
   | 0.7 | 235 | −59.04 | 21.31x |
   | 0.8 | 111 | −31.48 | 10.02x |

   PnL is negative across the *entire* grid and becomes less negative every time
   the threshold rises — i.e. every time the strategy trades less. If signal edge
   were the bottleneck, a higher-conviction threshold would help only up to a
   point and then the loss of trade count would hurt. Instead loss shrinks
   monotonically with turnover all the way down. That is the fingerprint of a
   strategy whose economics are dominated by transaction cost, not by alpha. The
   RESULTS page states it plainly: "PnL is negative across the whole grid and
   becomes *less* negative as the threshold rises (higher threshold → fewer
   trades → less cost/slippage bleed)."

The two views agree: the OBI signal has a thesis, but the strategy over-trades
relative to the size of its edge, and the ~21x turnover converts a small gross
positive into a net loss.

## Why turnover is the lever

The cost model is **5 bps transaction cost + 2 bps slippage per fill**
(documented in [docs/RESULTS.md](../RESULTS.md)). At 7 bps round-trip-ish cost
per fill, turning the book over 21 times is ~21 × the per-trip cost drag. With a
gross edge of only a few dollars across the window, even a handful of basis
points per fill, compounded across hundreds of fills, swamps it. The edge per
trade is real but thin; the strategy pays the spread far too many times to keep
it.

## Fix direction

Two complementary changes, both of which preserve the existing default behavior
until explicitly enabled (the cost model stays flat-fee unless a strategy opts
into the gate):

1. **Net-of-cost entry gate.** Don't take a signal unless its *expected* edge
   exceeds the modeled round-trip cost (transaction + slippage) by a margin. This
   filters out exactly the marginal trades the calibrate grid shows are
   destroying value — the same trades that vanish, profitably, as the threshold
   rises. The gate makes the threshold sweep's lesson structural instead of a
   parameter the operator has to remember to tune up.

2. **Maker execution.** Much of the 7 bps is taker cost and crossing the spread.
   Posting passively (maker) where the strategy's hold time allows would cut or
   even invert the per-fill fee, directly attacking the dominant term. The OBI
   signal's average hold (thousands of seconds — see the `avg_hold_seconds`
   column in the calibrate CSV) is long enough that resting orders are plausible
   rather than fantasy.

## The takeaway for a reviewer

The senior-signal here is not "we found a profitable strategy." It is the
opposite and more valuable: **we built the measurement that refuses to let a
good-looking gross number ship.** A 70% win rate and a 5.85 profit factor are
exactly the figures a less disciplined system would have promoted. Pairing every
gross metric with `net_trading_pnl`/`fee_drag_pct`, and reading the calibrate
grid as a turnover-vs-cost diagnostic rather than an alpha search, is what turned
"this works" into the correct conclusion: "this has an edge, but it over-trades,
and here is the fix." See [docs/RESULTS.md](../RESULTS.md) for the unedited
backtester and calibrator output behind every number above.
