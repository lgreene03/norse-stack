# Case study: two contradictory equity figures, two real accounting bugs

**One line:** the backtester reported a strategy as both **−10.8% terminal value**
and **+4.48% return** at the same time. Those can't both be true. Chasing the
contradiction down uncovered two genuine bugs in `Portfolio.Snapshot` /
`StrategyTotalReturn` that had corrupted the equity curve, drawdown, and every
derived return — including the live operator dashboard.

## The smell: two numbers that can't coexist

An early draft of [docs/RESULTS.md](../RESULTS.md) reported the OBI run as:

```
Final Value:     89,153.86      (on initial cash of 100,000 → −10.8%)
Strategy Return: +4.48%
Excess Return:   +5.06%
```

A final value that is 10.8% *below* starting cash cannot coexist with a *positive*
+4.48% strategy return. One of the two had to be wrong — and as it turned out,
**both** were. The inconsistency is what flagged the bug; a single plausible-looking
number would have shipped unquestioned. This is the entire argument for carrying a
benchmark and re-deriving by hand: they catch your own measurement bugs.

## Re-deriving by hand against the trade journal

The check was to ignore the engine's summary and reconstruct equity from first
principles using the append-only trade journal (`data/backtest_trades.jsonl`):

- Net open position at the end of the run: only **~0.03 BTC** — a tiny residual
  long, not a 10%-of-account inventory swing.
- Realized PnL: **−59.04** (matches the calibrate grid's `obi,0.01,0.7` row in
  [docs/RESULTS.md](../RESULTS.md)).
- Fees paid: on the order of **$91** across the run.

A near-flat book with realized −59 and ~$91 of fees should end *just below*
100,000 — somewhere around 99,9xx. It absolutely should not be at 89,153 (that
would require ~$10.8k of value to have evaporated with no position to lose it on),
and it should not show +4.48% either. Neither reported figure survived the
hand re-derivation, which meant the engine had (at least) two independent errors.

## Root cause #1 — `Portfolio.Snapshot` used `cash + unrealizedPnL`

In [`huginn/internal/portfolio/portfolio.go`](https://github.com/lgreene03/huginn/blob/main/internal/portfolio/portfolio.go),
`Snapshot` computed total equity as **`cash + unrealizedPnL`** instead of
**`cash + market value of open positions`**.

The accounting error: when the strategy buys, cash *already* pays out the
position's full cost basis. The position is now worth its market value, which is
`cost basis + unrealizedPnL`. Adding back only the *unrealized PnL* silently drops
the cost-basis term — so total value is understated by the entire cost basis of
any open inventory.

When flat this bug is invisible (`positionsValue == 0`, so `TotalValue == cash`),
which is exactly why it survived: most test states are flat. But the OBI run ends
holding an accumulated long, because its sells are clipped by the long-only rule
and inventory piles up. That open position's cost basis — roughly **$10.8k** —
vanished from `Final Value`, producing the impossible 89,153.86. The fix sets
`snap.TotalValue = snap.Cash + positionsValue`, where `positionsValue` marks every
open position to market. The code now carries a comment block at that line
(`portfolio.go` ~L184–189) documenting the trap precisely so it can't regress
silently.

### The unit test had encoded the *wrong* value

Worse, a unit test had *baked in* the buggy behavior: it asserted that a
fair-value buy "halved" the account value (cash drops by the purchase, and the
test expected total value to drop with it instead of staying constant because the
position is worth what was paid). A buy at fair value must leave total equity
*unchanged* — cash down, position value up by the same amount. The test was
asserting the symptom of the bug as if it were correct. It was corrected as part
of the fix, and that is the most dangerous form of this class of bug: a wrong
test that actively defends the wrong behavior.

## Root cause #2 — `StrategyTotalReturn` measured from `equity[0]`

The second bug was in `StrategyTotalReturn`: it measured return from
**`equity[0]`** — the first *daily-sampled* equity point, which is a mid-window
mark — instead of from the **initial cash cost basis**.

Two consequences:

1. It was no longer comparable to the buy-and-hold benchmark, which *is* measured
   from initial cash. Comparing a return measured from a mid-window mark against a
   benchmark measured from inception is apples-to-oranges.
2. Because `equity[0]` is itself a point on the (corrupted) equity curve, a
   genuinely losing run could be reported as positive — which is exactly how a
   −10.8% terminal value coughed up a +4.48% return.

The fix measures `finalValue / initialCash − 1`, the same basis as the benchmark.

## After the fix, the numbers reconcile

With both bugs fixed the OBI run reports, consistently:

```
Final Value:     99,939.37
Strategy Return: -0.06%
Excess Return:   +0.51%
```

A final value just below 100,000 and a slightly-negative return — exactly what
realized −59 plus fees on a near-flat book should produce. The two figures now
agree with each other and with the hand re-derivation.

## Regression coverage added

To stop this class of error returning:

- The corrected `Snapshot` unit test now asserts the *invariant* — a fair-value
  buy leaves total equity unchanged (cash down, market value up by the same
  amount), and a flat book has `TotalValue == cash`.
- Tests cover the open-inventory case specifically (where the old bug was
  invisible to flat-state tests), so cost basis must be marked to market.
- `StrategyTotalReturn` is tested against the initial-cash basis so it can never
  silently re-anchor to a mid-window equity point.

## Why this matters beyond one doc page

These bugs did not just dirty a README table. `Snapshot.TotalValue` feeds the
equity curve, the drawdown calculation, and every return derived from them —
including the **live operator dashboard**. Any time a position was open, the
operator's view of account value was wrong by the open cost basis. The
contradiction in [docs/RESULTS.md](../RESULTS.md) was the visible tip; the
correctness win is in the live system. The episode is left in the public record
on purpose: the value of a benchmark plus a skeptical hand re-derivation is that
they catch *your own* measurement bugs before they reach capital.
