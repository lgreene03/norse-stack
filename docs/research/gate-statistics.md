# Gate statistics (G1.3)

Owner: quant specifies, technical implements. Written 2026-11-12. The code is in
[research/norse_research](../../research/norse_research) (`sharpe.py`, `cscv.py`,
`block_bootstrap.py`, `robustness.py`, `benchmark.py`) and the tests in
[research/tests](../../research/tests), run by CI's `research` job.

These are the statistics the evaluation gate in [NEXT_ITERATION.md](../NEXT_ITERATION.md)
names: the deflated Sharpe ratio on the joined daily out-of-sample returns with N from
the trial ledger, the combinatorially symmetric PBO over 16 blocks, the minimum track
record length, 20-day block bootstrap intervals, the Sharpe ratio with each year and
with the best 20 days removed, and the volatility-matched equal-weight buy-and-hold
benchmark. Each has a known-answer test and a synthetic check, and this page says which
known answers come from a published worked example and which do not.

Everything here was computed on synthetic data or is arithmetic. No price, return,
signal or performance figure was computed on market data, and nothing was read from the
holdout, because the pre-registration (G0.7) is not signed.

The trial ledger (G1.5) does not exist yet, so N and the trials' return series are
parameters of these functions. G1.5 must feed them from the ledger, with N the count of
trial entries under the counting rule registered in `prereg-v1`
([ledger-threat-model.md](ledger-threat-model.md), principle 3), and must wrap these
functions in the gate's public functions, which take the ledger and have no N parameter
(threat T19, `test_statistics_take_n_only_from_the_ledger`). Nothing here reads a
ledger.

## Summary

| Statistic | Function | Source | Known answer | Published worked example? |
|---|---|---|---|---|
| Deflated Sharpe ratio | `sharpe.deflated_sharpe`, `deflated_sharpe_ratio` | Bailey and Lopez de Prado 2014 | SR_0 = 0.1132 and DSR = 0.9004 at N = 100; 0.9505 at N = 46; 0.9505 at N = 88 with normal returns | Yes |
| Probabilistic Sharpe ratio | `sharpe.probabilistic_sharpe_ratio` | Bailey and Lopez de Prado 2012 | PSR(0) = 0.982, 0.913 and 0.953 for the paper's hedge fund | Yes |
| Minimum track record length | `sharpe.min_track_record_length` | Bailey and Lopez de Prado 2012 | 59.895 months; twelve entries of Figures 8 to 11 | Yes |
| CSCV PBO | `cscv.cscv` | Bailey, Borwein, Lopez de Prado and Zhu 2017 | 12,870 splits; six rows of the paper's accuracy table | Yes, as a simulation study |
| Block bootstrap intervals | `block_bootstrap.block_bootstrap` | Politis and Romano 1992 | Exact bootstrap mean and variance of the mean, by enumeration | No: closed form |
| Leave one year out, minus best 20 days | `robustness.robustness` | NEXT_ITERATION.md | Hand computations | No: the gate's own rule |
| Volatility-matched buy-and-hold | `benchmark.*` | NEXT_ITERATION.md | Hand computations | No: the gate's own construction |

## Conventions

Returns are simple daily returns, net of costs. Every Sharpe ratio inside the code is
per observation (daily), never annualised, because the formulas are stated that way;
the gate annualises with 365 days for display (`sharpe.PERIODS_PER_YEAR`), since crypto
trades every day. The Sharpe ratio is the mean over the sample standard deviation
(divisor n - 1). Skewness is m3 / m2^1.5 and kurtosis m4 / m2^2, from central moments
with divisor n, and kurtosis is not excess: a normal distribution has 3, as in the
papers. A Sharpe ratio that is undefined (fewer than two returns, or no variance) raises
or is reported as undefined, never as zero, except inside CSCV, where a trial that is
flat throughout a half scores zero so it can still be ranked.

## The deflated Sharpe ratio

**Source.** D. H. Bailey and M. Lopez de Prado, "The Deflated Sharpe Ratio: Correcting
for Selection Bias, Backtest Overfitting and Non-Normality", Journal of Portfolio
Management 40(5), 2014, Eq. (2), checked against the authors' copy on
davidhbailey.com.

**Formula.** The DSR is a probabilistic Sharpe ratio whose benchmark is the Sharpe ratio
the best of N skill-less trials is expected to reach:

    SR_0 = sqrt(V) * [ (1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e)) ]
    DSR  = Phi( (SR - SR_0) * sqrt(T - 1) / sqrt(1 - skew * SR + (kurtosis - 1) / 4 * SR^2) )

with SR, skew, kurtosis and T those of the selected strategy's returns, V the variance of
the trials' Sharpe estimates, g the Euler-Mascheroni constant and Phi the standard normal
distribution. The bracket is the same E[max Z_N] approximation the minimum backtest
length uses, so the code reuses `norse_research.minbtl.expected_max_z` rather than
repeating it. For one trial the bracket is 0, the exact expectation, and the DSR is the
PSR against zero.

**Known answer, published.** The paper's "A numerical example": an annualised Sharpe
ratio of 2.5 over five years of daily returns (T = 1250 at 250 a year), N = 100 trials
whose annualised Sharpe ratios have variance 1/2, skewness -3 and kurtosis 10. The paper
gives SR_0 = 0.1132 (not annualised) and DSR = 0.9004, so the strategy is rejected at
95%; it would have passed with 0.9505 had only N = 46 trials been run; and with normal
returns (skewness 0, kurtosis 3) the DSR is 0.9505 after N = 88. The code gives
0.11317, 0.90040, 0.95050 and 0.95049, and confirms that 46 and 88 are the last N to
pass (`test_dsr_paper_numerical_example_n100`, `..._n46_is_the_last_pass`,
`..._normal_returns_n88` in `test_sharpe.py`).

**How the gate applies it.** `sharpe.deflated_sharpe` takes the candidate's joined daily
out-of-sample returns, every walk-forward fold concatenated into one series, and reads
SR, skewness, kurtosis and T from that series. It never computes a statistic per fold
and averages it, which is what huginn did (see "What these are not" below). N is the
ledger's count. For V the function takes the larger of the null variance 1 / (T - 1) and
the cross-trial variance of the N trials' Sharpe ratios, when those are supplied. The
reasons are under "Decisions" below.

**Pass marks.** With the null variance and normal returns, the smallest annualised net
Sharpe ratio whose DSR exceeds 0.95 (`sharpe.min_passing_sharpe`):

| Joined out-of-sample years | T | N = 1 | N = 10 | N = 20 | N = 30 |
|---|---|---|---|---|---|
| 4.30 (the MinBTL floor for 30 trials) | 1,570 | 0.79 | 1.55 | 1.71 | 1.80 |
| 5.6 (NEXT_ITERATION.md's figure) | 2,044 | 0.70 | 1.36 | 1.50 | 1.57 |
| 6.67 (H1 after a one-year first window) | 2,435 | 0.64 | 1.25 | 1.37 | 1.44 |

The 5.6-year row reproduces NEXT_ITERATION.md's "roughly 1.6 at N = 30, or 1.4 at N = 10"
(`test_pass_marks_match_next_iteration`). Daily skewness and kurtosis barely move these:
with skewness -1 and kurtosis 8 the 4.30-year marks are 1.59, 1.75 and 1.84, because the
non-normal terms are multiplied by a daily Sharpe ratio near 0.08. G0.7's table should be
computed at the out-of-sample length its fold design actually leaves, since at the
4.30-year floor the mark at N = 30 is 1.80, not 1.6.

**Synthetic checks.** For one skill-less strategy (true Sharpe ratio zero, normal
returns, 250 days, 2,000 samples) PSR(0) exceeds 0.95 in 5.6% of samples, as a uniform
p-value should (`test_psr_is_uniform_under_the_null`). The best of ten skill-less trials
over two years passes the gate's DSR in 0 of 200 simulations, and one trial with an
annualised Sharpe ratio of 3 among nine skill-less ones over four years is detected in
50 of 50 (`test_dsr_rarely_passes_the_best_of_skill_less_trials_and_finds_a_planted_edge`).
The full calibration at N = 30 over 1,000 ledgers is G1.7's, not this page's.

**What it cannot detect.** The DSR assumes the trials' returns are independent over time;
autocorrelated daily returns make its standard error too small, which is one reason for
the block bootstrap below. N counts trials, not independent trials, so correlated trials
make it strict rather than lenient. It says nothing about whether a strategy that passed
on 2018 to 2025 will work later.

## The probabilistic Sharpe ratio and minimum track record length

**Source.** D. H. Bailey and M. Lopez de Prado, "The Sharpe Ratio Efficient Frontier",
Journal of Risk 15(2), 2012, Eq. (11) for the PSR and Eq. (13) for MinTRL, checked
against the authors' copy on davidhbailey.com.

**Formulas.**

    PSR(SR*) = Phi( (SR - SR*) * sqrt(n - 1) / sqrt(1 - skew * SR + (kurtosis - 1) / 4 * SR^2) )
    MinTRL   = 1 + (1 - skew * SR + (kurtosis - 1) / 4 * SR^2) * (Phi^-1(confidence) / (SR - SR*))^2

MinTRL is in observations; divide by 365 for years of daily returns. It is infinite when
SR does not exceed SR*, since no length of record makes it significant.

**Known answers, published.** Section 3 and Figure 6: a hedge fund's two-year monthly
record with SR = 0.458 a month, skewness -2.448 and kurtosis 10.164 has PSR(0) = 0.982
assuming normality, 0.913 with its skewness and kurtosis, and 0.953 with three years
instead of two. The code gives 0.9817, 0.9134 and 0.9535; the paper computed from
unrounded inputs and prints three places, so the test allows one unit in the third
(`test_psr_paper_hedge_fund_example`). Appendix A.3: a monthly SR of 2/sqrt(12) against
1/sqrt(12), skewness -0.72, kurtosis 5.78, 95% confidence, needs MinTRL = 59.895 months,
and the PSR over 59.895 observations is 0.95; the code gives 59.8951 and 0.95
(`test_mintrl_paper_appendix_example`). Figures 8 to 11 tabulate MinTRL in years; twelve
entries across all four tables (daily, weekly, monthly normal, and monthly with the
appendix's moments) match to the printed two places
(`test_mintrl_paper_figures_8_to_11`). The daily table uses 252 days a year: its 5 against
4.5 entry is 11.36 at 252 and would be 11.37 at 250.

**For the gate.** NEXT_ITERATION.md and G2.4 call for the minimum track record length in
tear sheets: the out-of-sample length at which a candidate's observed Sharpe ratio would
be significant against zero (or against SR_0, for the deflated version). It is the same
algebra as the PSR, so the PSR tests cover it too. Its synthetic check is the PSR's.

## The probability of backtest overfitting (CSCV)

**Source.** D. H. Bailey, J. M. Borwein, M. Lopez de Prado and Q. J. Zhu, "The
Probability of Backtest Overfitting", Journal of Computational Finance 20(4), 2017,
Section 2.2 (Algorithm 2.3) and Section 3.1, checked against the copy on
davidhbailey.com. The accuracy study used as the known answer is in the revised version
of November 2014 on Borwein's site (carmamaths.org), Sections 6 and 7 and Figure 6; in
the published version it moved to appendices at SSRN 2568435.

**Method.** Take the T by N matrix of daily returns, one column per trial, rows on the
same dates. Cut the rows into S = 16 contiguous blocks. For each of the C(16, 8) = 12,870
ways of choosing eight blocks as the in-sample half J: find the trial with the best
Sharpe ratio on J, find its rank r among all N trials' Sharpe ratios on the other eight
blocks, set w = r / (N + 1), and record the logit ln(w / (1 - w)). PBO is the share of
splits whose logit is at most zero: the in-sample winner did no better than the median
trial out of sample. The paper's text says S = 16 gives "12,780" combinations; that is a
transposition, and the test pins 12,870 (`test_gate_uses_16_blocks_and_12870_splits`).

The code keeps, per block and trial, the sum and the sum of squares of the returns, so a
split adds eight numbers per trial rather than re-reading 1,500 to 2,500 days. Fixing the
first block in J visits each pair of halves once and evaluates both orientations. One
computation over a 10 by 1,000 matrix takes about 0.07 seconds, and 30 trials over 2,000
days about 0.14 seconds, in pure Python. It also returns the share of splits in which the in-sample
winner loses money out of sample (the paper's probability of loss) and the logits.

**Known answer, published simulation study.** The accuracy study builds each matrix from
N - 1 trials rescaled to an annualised Sharpe ratio of exactly zero and one trial rescaled
to exactly SR_Case, over T days at 365.25 x 5/7 a year (the paper's Snippet 1), and
prints, for each (SR_Case, T, N), the true probability two ways (Prob_MC from 1,000
first-half and second-half experiments, Prob_EVT from extreme value theory) and the mean
and standard deviation of its own CSCV estimates over 1,000 matrices. The test rebuilds
40 matrices per row the same way:

| SR_Case | T | N | Ours, mean of 40 | Paper's CSCV mean (sd) | Prob_MC | Prob_EVT |
|---|---|---|---|---|---|---|
| 0 | 1000 | 10 | 1.000 | 1.000 (0.000) | 1.000 | 1.000 |
| 1 | 500 | 10 | 0.632 | 0.618 (0.054) | 0.650 | 0.608 |
| 1 | 1000 | 10 | 0.380 | 0.399 (0.054) | 0.354 | 0.360 |
| 1 | 2500 | 10 | 0.092 | 0.123 (0.048) | 0.093 | 0.086 |
| 2 | 500 | 10 | 0.147 | 0.186 (0.054) | 0.146 | 0.137 |
| 2 | 1000 | 10 | 0.021 | 0.041 (0.027) | 0.011 | 0.023 |

The test (`test_paper_accuracy_table`) requires each mean within 0.035 of Prob_EVT,
within 0.05 of Prob_MC, and within one of the paper's standard deviations of the paper's
own CSCV mean. Every row is closer to the paper's two measures of the truth than the
paper's own CSCV was: the paper's CSCV means sit 0.00 to 0.05 above its EVT values, which
it describes as erring on the conservative side. Ours need not: the rows of these
matrices are exchangeable, so every balanced split has the hold-out's probability of
overfitting and the expected CSCV PBO equals Prob_MC exactly. What made the paper's
estimates higher is not stated (its value of S for this study is not given either; it
does not change the expectation). Rows with N = 50 and 100 agree in the same way but take
too long for CI. The paper's other worked example, the seasonal strategy of its Section 6
(PBO 55% and 13%), depends on one random path the paper does not publish, so it cannot
be reproduced.

**Hand-computed checks.** With S = 2, a trial that is best on the first half and worst on
the second gives logits ln(1/2) and PBO 1, a trial that stays best gives ln 2 and PBO 0,
identical trials give logit 0 (counted as overfit), and a flat trial scores zero between a
winner and a loser (the four tests named for these cases in `test_cscv.py`).

**Synthetic checks.** For IID noise trials (not rescaled) and even N the in-sample
winner's out-of-sample rank is uniform, so the expected PBO is exactly 1/2; over 60
matrices of ten trials it averages 0.478 (`test_pure_noise_trials_average_half`). One
trial with an annualised Sharpe ratio of 3 among nine noise trials over four years gives
PBO 0 and probability of loss 0 (`test_one_planted_strong_trial_gives_low_pbo`).

**What it cannot detect.** PBO measures whether choosing the in-sample best helps out of
sample; it is not a test of skill. On pure noise a single matrix's PBO ranged from 0.06 to
0.94 across those 60 matrices and was below 0.5 in 34 of them, so the gate's PBO < 0.5 is
passed by roughly half of all no-skill ledgers on its own. The DSR and the benchmark comparison are what reject them.
And if every trial shares one edge, PBO can be high while all of them are good (the paper
makes the same point in Section 5.2).

## Block bootstrap intervals

**Source.** D. N. Politis and J. P. Romano, "A circular block-resampling procedure for
stationary data", in Exploring the Limits of Bootstrap, Wiley, 1992, pp. 263-270; the
moving block bootstrap it modifies is H. R. Kunsch, Annals of Statistics 17(3), 1989.

**Method.** Strategy returns are autocorrelated (positions last days to weeks), so
resampling single days understates uncertainty. The circular block bootstrap wraps the
series into a circle, draws ceil(T / 20) blocks of 20 consecutive days from uniformly
random starting days, joins them and cuts the result to T days; the interval is the
percentile interval of the resampled mean or Sharpe ratio. Prefix sums make each
replicate cost ceil(T / 20) lookups. The seed is a required argument, because a verdict
must reproduce exactly from a clean checkout (P1). The function also returns the
standard deviation of the resampled Sharpe ratios.

**Known answer: none published, closed form instead.** No published worked example with
numbers for a block bootstrap interval was found, so this statistic has no published
known answer. What is published is a theorem: for the circular block bootstrap the
resampled mean is unbiased for the sample mean, and its variance is (1/k) times the
variance of the n circular block means, with k = T / L blocks. The test enumerates all
216 equally likely resamples of a six-day series in blocks of two and finds both exactly
(`test_exact_bootstrap_mean_and_variance_by_enumeration`), and checks that the random
resampling path reproduces the closed-form spread on a 200-day series
(`test_random_resampling_matches_the_exact_variance`).

**Synthetic checks.** On AR(1) returns with autocorrelation 0.5, 1,000 days and 200
series, 90% intervals with 20-day blocks cover the true mean 84.5% of the time and the
true Sharpe ratio 85.5% (`test_coverage_near_nominal_with_20_day_blocks`). Resampling
single days instead covers the mean only 57% of the time, as the long-run variance being
three times the daily one predicts
(`test_single_day_resampling_undercovers_autocorrelated_returns`).

**What it cannot detect.** The intervals run a little narrow (about five points short of
nominal on that series), as block bootstrap intervals do when the dependence reaches
beyond a block or the sample is short. They describe sampling noise in one path of
returns; they say nothing about selection across trials, which is the DSR's job.

## Leave one year out, and minus the best 20 days

**Source.** NEXT_ITERATION.md: "The Sharpe stays positive with each year removed and with
the best 20 days removed." These are the gate's own criteria, not a published statistic,
so there is no paper and no published worked example; the known answers are hand
computations.

**Definitions.** A year is a UTC calendar year of the return dates; each calendar year
present, a partial first or last year included, is removed once and the Sharpe ratio
computed on the rest. The best 20 days are the 20 highest daily returns (an exact tie at
the cut removes the earlier day), and they are removed, not set to zero. An undefined
Sharpe ratio, for example a series with a single calendar year or nothing left but flat
days, fails the check.

**Known answers, by hand.** Returns of 0.01 and 0.03 in 2024 and -0.01, 0.02 and 0.05 in
2025 give 2/3 without 2024 and sqrt(2) without 2025
(`test_leave_one_year_out_by_hand`); removing the two best of 0.05, -0.01, 0.02, 0.05 and
0.00 leaves a Sharpe ratio of (1/300) / sqrt(7/30000) (`test_minus_best_days_by_hand`).

**Synthetic checks.** A steady edge (annualised Sharpe ratio 2 for five years) passes; an
edge that comes from one strong year fails when that year is removed; a slightly losing
strategy with 15 very large winning days is positive overall and negative without its
best 20 days (the three tests named for these cases in `test_robustness.py`).

**What it cannot detect.** A strategy that is mediocre in every year passes both checks;
they find concentration, not weakness, which is left to the DSR.

## The volatility-matched, equal-weight buy-and-hold benchmark

**Source.** NEXT_ITERATION.md: the deflated Sharpe criterion must also hold "for returns
in excess of a volatility-matched, equal-weight buy-and-hold of the same universe, because
a long-flat trend rule can otherwise pass on market exposure alone". A construction the
gate defines, so no published worked example exists; the known answers are hand
computations.

**Definitions.** At the start of each holding period the benchmark holds that period's
universe in equal weights and then buys and holds, its weights drifting with prices until
the next period, when it rebalances to equal weights over the new universe. For H1 a
period is a month and the universe that month's dated top five, delisted pairs included.
Costs are optional (`cost_per_side`, charged on each rebalance's turnover, the first
purchase included) and default to zero, which makes the benchmark harder to beat; G0.7
fixes which the gate uses. A held asset with no return raises an error: how a halted or
delisted holding is valued is G0.7's decision (data-coverage finding 2), and this code
never invents an exit price. Volatility matching multiplies the benchmark's returns by one
constant, the strategy's sample standard deviation over the benchmark's on the same days,
so both have the same realised volatility; that leaves the benchmark's Sharpe ratio
unchanged and sets how much market exposure the excess return removes. The gate applies
the deflated Sharpe to `excess_returns(strategy, volatility_matched(benchmark,
strategy))`.

**Known answers, by hand.** Two assets returning 10%, 0% and -5%, and 0%, 10% and 0%,
held half each, give 5%, 4.762% and -2.5%, compounding to the 7.25% that holding the two
separately gives (`test_buy_and_hold_drifts_by_hand`); a rebalance from A and B to B and
C at 10 bps a side costs 0.1% of turnover 1.0, as does the first purchase
(`test_rebalance_to_a_new_universe_with_costs_by_hand`); a strategy that is exactly half
the benchmark has zero excess return after matching (`test_volatility_matching_by_hand`).

**Synthetic checks.** In a rising, volatile three-coin market over four years, a long-flat
rule in the market on random days has an annualised Sharpe ratio of 0.97 but -0.41 in
excess of the matched benchmark, so market exposure alone fails
(`test_market_exposure_alone_does_not_beat_the_benchmark`); the same rule with foresight
on a third of its days has a positive excess return
(`test_a_planted_timing_edge_beats_the_benchmark`).

**What it cannot detect.** Matching uses the whole evaluation window's volatility, which an
evaluation benchmark may do but a trader could not. A strategy that times volatility
rather than direction is compared with a constant-exposure benchmark, which is the
intended comparison but not the only reasonable one.

## What these are not

huginn's walk-forward tools reported figures named "PBO" and "deflated Sharpe" that are
different quantities: a leave-one-fold-out PBO proxy, the DSR formula applied to each fold
and averaged across folds, and on huginn's `/api/validation` the share of folds with
non-positive out-of-sample PnL. Under G1.3 they are renamed in
[huginn#47](https://github.com/lgreene03/huginn/pull/47) to `FoldPBOProxy`,
`MeanFoldDeflatedSharpe` and `UnprofitableFoldShare`, with matching JSON keys and printed
labels. Earlier documents in this repository that quote "PBO = 1.00" for the retired
order-book strategy quote huginn's fold proxy, not CSCV.

## Decisions made here

The specification left these open. Each is the quant role's call, made before any trial
and on synthetic data only, and each is for G0.7 to adopt into `prereg-v1` or change
before the first trial.

**The variance in SR_0.** The paper uses the variance of the trials' Sharpe estimates. The
pass marks in NEXT_ITERATION.md use the null variance 1 / (T - 1), the only one known
before any trial. The gate takes the larger of the two. With correlated trials the
cross-trial variance falls below the null one, and the paper's form would then lower the
bar below the published pass mark; the floor stops near-duplicate trials doing that, while
trials that genuinely disperse raise SR_0 as the paper intends. Under the null with
independent trials the two agree.

**Joined, not averaged.** The DSR is computed once, on the candidate's daily
out-of-sample returns with every fold joined, with skewness, kurtosis and T read from that
series.

**CSCV details.** Blocks differ in length by at most one row when 16 does not divide T
(dropping rows would compute PBO on different data from the DSR); the in-sample winner
among equals is the lowest-indexed trial; tied out-of-sample scores share their mean rank;
a logit of exactly zero counts as overfit; a trial flat over a half scores zero. The CSCV
matrix's columns are the N ledger trials' joined out-of-sample returns on common dates.

**Robustness and benchmark details.** Calendar years in UTC, partial years included; best
days removed rather than zeroed; undefined counts as failing; benchmark costs default to
zero; a held asset without a return is an error.

**N as a parameter.** The brief asked for N as a parameter until G1.5; the G1.4 threat
model asks that the gate's public functions take no N. Both hold if G1.5 wraps these
functions, which is how the module docstrings describe it.

## Findings

Each with severity (what goes wrong if ignored) and confidence.

1. **Medium, confirmed by arithmetic.** NEXT_ITERATION.md's pass marks ("roughly 1.6 at
   N = 30, or 1.4 at N = 10") assume 5.6 years of joined out-of-sample returns. At the
   4.30-year floor that data-coverage.md sets for 30 trials, they are 1.80 and 1.55. G0.7's
   table must be computed at the out-of-sample length its fold design leaves.
2. **Medium, confirmed.** The variance in SR_0 was unspecified, and the paper's choice can
   be made more lenient than the published table by running near-duplicate trials. Resolved
   above with a floor; G0.7 must adopt or change it, and G1.7 calibrate whichever is chosen.
3. **Medium, confirmed in simulation.** PBO < 0.5 is not evidence of skill: on pure noise a
   single matrix's PBO ranged from 0.06 to 0.94, and about half pass. It must never be
   quoted as a pass on its own. G1.7's calibration should report each criterion's no-skill
   pass rate separately as well as the gate's.
4. **Medium, confirmed.** huginn's console contract changes with huginn#47: norse-stack's
   console reads `pbo` and `deflatedSharpe` from huginn's `/api/validation` and research
   runs, and once a rebuilt huginn runs it will show "n/a" there. The interface role should
   relabel or remove that panel (G0.12, G1.8).
5. **Low, confirmed.** The 20-day block bootstrap's 90% intervals covered 84.5% (mean) and
   85.5% (Sharpe ratio) on AR(1) returns with autocorrelation 0.5. Tear sheets should say
   the intervals may run slightly narrow.
6. **Low, confirmed.** The paper's text gives "12,780" combinations for S = 16 (and the
   CRAN `pbo` package's README repeats it); the correct count is 12,870, as G1.3 says.
7. **Low, likely.** The paper's own CSCV estimates in its accuracy study run up to 0.05 above
   its Monte Carlo and EVT values, while ours match those values; the paper does not state
   the implementation detail that differs. Our code is the more accurate of the two on that
   study, but a reviewer comparing with the paper's CSCV column will see the gap.
8. **Low, confirmed.** No published worked example was found for the block bootstrap, the
   robustness checks or the benchmark; their known answers are a theorem's closed form and
   hand computations, and are labelled as such.
9. **Low, confirmed.** The skewness and kurtosis terms barely move the pass marks for daily
   returns (1.80 becomes 1.84 at skewness -1 and kurtosis 8), so a strategy's tails
   neither rescue nor sink it in the DSR at this frequency; of the gate's criteria, the
   minus-best-20-days check is the one that looks at the tails.

## Reproduce

From `research/`:

```bash
python -m pytest tests/ -q        # every known-answer and synthetic check, about 20 s
ruff check .                      # with the ruff pinned in requirements-dev.txt
```

The papers were read on 2026-11-11 from the authors' copies: davidhbailey.com
(`deflated-sharpe.pdf`, `sharpe-frontier.pdf`, `backtest-prob.pdf`) and carmamaths.org
(`jon/backtest2.pdf`, the November 2014 revision with the accuracy study).
