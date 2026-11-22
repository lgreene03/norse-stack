# Gate calibration (G1.7)

Owner: quant. First written on 2026-11-21 for `prereg-v1`; rewritten on 2026-11-22 for
`prereg-v2`, before its rerun. The study is
[research/norse_research/calibration.py](../../research/norse_research/calibration.py), its
tests [research/tests/test_calibration.py](../../research/tests/test_calibration.py), and
the rerun's output
[research/calibration/gate-calibration-results.json](../../research/calibration/gate-calibration-results.json).
Everything here was computed on synthetic data. No price, return or performance figure was
computed on market data, nothing was read from the development or holdout files, and the
dataset store was never opened; the one repository file the study reads is
`docs/preregistration.json`, for the registered windows, grid sizes and budget and for the
ids of the families `gate.calibration` judges.

## Status

This report covers two full runs of one study, on the same families and at the same seed.

**The `prereg-v1` run (2026-11-21), kept as disclosed history.** It calibrated the gate as
`prereg-v1` registered it, with V the sample variance of every selectable run's Sharpe ratio
across both hypotheses, against the clause "detects a planted Sharpe of 1.5". The gate was safe,
passing at most 13 of 1,000 no-skill ledgers in any family, but it did not detect a planted 1.5:
the planted hypothesis passed development in 144 to 335 of 1,000 ledgers in the four families,
where more than 500 were required. Its results file is
[gate-calibration-results-prereg-v1.json](../../research/calibration/gate-calibration-results-prereg-v1.json),
SHA-256 `2eb307f3da3b425a036bf711207e2670c9641381e1a369d85094ad70037c4d9e`, and its report is
"History: the `prereg-v1` run" below. On its findings the owner revised V (decision D22) and
decided what a failed rerun means (D24).

**The rerun under `prereg-v2`.** It calibrates the gate as `prereg-v2` registers it, with V the
null variance 1/(T - 1) of the candidate's window, against the restated clause in section 10 of
[PREREGISTRATION.md](../PREREGISTRATION.md) and `gate.calibration` in
[preregistration.json](../preregistration.json) ("The clause the rerun is judged by", below).
The rerun is decisive. The lead tags `prereg-v2` once it is recorded, whatever it finds (section
16); if the gate does not meet the clause, `prereg-v2` is tagged as it stands, the gate is not
revised again in this iteration, and the iteration closes NOT JUDGED (D24). So the study was
fixed, committed and pushed before the run started, and nothing in it, in the results or in the
thresholds was changed after it. The one exception is disclosed under "What had been seen before
the rerun": after the first complete run, the order in which the results section lists the four
detection families was fixed, and the study was run again from scratch, which had to reproduce
the first complete run's results file byte for byte. Its verdict is the first paragraph of
"Results of the rerun", which the run itself wrote.

## The clause the rerun is judged by

Fixed on 2026-11-22, before the rerun, in this section and in the code (`calibration.verdict`),
and committed and pushed before the run started; the run record under "Reproduce" names that
commit. Section 10, "Calibration", registers it, and `gate.calibration` (`condition`,
`no_skill`, `detection`), which governs, lists the families by id:

> No strategy is judged until both halves hold, at N = 30, in the families of G1.7's study at
> #66's commit c2365ee (`norse_research.calibration.FAMILIES`, seed 20261121, 1,000 ledgers
> each), unchanged for the rerun except V, which becomes the null variance, and the detection
> constants, 2.0 in place of 1.5. No skill: in every one of the 17 no-skill families
> separately, the gate produces a holdout candidate in at most 50 of 1,000 simulated ledgers
> (5%); the two serially correlated families are reported only. Detection: in each of the four
> single-planted families at an annualised Sharpe ratio of 2.0 (`single-H1-s2-low`,
> `single-H1-s2-high`, `single-H2-s2-low` and `single-H2-s2-high`), the planted hypothesis
> passes development (its candidate meets DEV-1 to DEV-5) in more than 500 of the 1,000
> ledgers; the minimum of the four decides.

How the study reads it:

1. **Which families.** `calibration.verdict` takes the 17 `no_skill_families` and the four
   `detection_families` by the ids `gate.calibration` lists, read from
   `docs/preregistration.json`, never by a family's attributes, so no family added later could
   join either half. The two `sensitivity_families` (`ar0.1-w0-a0` and `ar0.1-w0.9-a0.9`, every
   shock AR(1) at 0.1) are outside "every one of the no-skill families" and reported only. A
   results file without every one of the 21 judged families gives no verdict.
2. **The no-skill half.** "Passes" is the gate producing a holdout candidate, the full
   development gate including the candidate rule (reading 1 of the `prereg-v1` run,
   unchanged). "At most 50 of 1,000" is `rate <= FALSE_PASS_LIMIT` with 0.05, in every family
   separately.
3. **The detection half.** The metric is `planted_passes` at N = 30, the one committed in
   9ac814f: for a single-planted family, the planted hypothesis's candidate meets DEV-1 to
   DEV-5. "More than 500 of the 1,000" is `rate > DETECTION_POWER` with 0.5, so 500 fails and
   501 passes; the minimum decides because every one of the four must clear it. Whether that
   candidate is the planted configuration is reported beside it (`planted_column_passes`) and
   not judged.
4. **What is reported only.** Power at 1.5 (the four single-planted families at 1.5), at
   N = 14, for an edge shared within one hypothesis and for one shared by both; HOLD-1 end to
   end; the serially correlated families; and `prereg-v1`'s pooled V at N = 30 as a
   counterfactual (`counterfactual_pooled_v`), which no verdict reads.

**What changed from c2365ee, and what did not.** Changed: V at N = 30 and N = 14 is the null
variance, as `gate.trial_sharpe_variance` returns it (`judge`'s default); `DETECTION_SHARPE` is
2.0; `verdict` selects by the registered ids, its 1.5 keys are renamed (`detection_planted_passes`
and `detection_met`) and it adds `complete`, `no_skill_gate_passes` and
`weakest_detection_family`; the unregistered counterfactual with V over each hypothesis's own
runs is dropped, and `prereg-v1`'s pooled V is kept as a labelled counterfactual; and the results
section of this report is written by the run (`--report`) and checked by a test against the
committed file, listing the detection families in registered order whether rendered from the
results in memory or from the file. Not changed: the 54 families, the seed 20261121, the 1,000 ledgers per family,
the simulation and its constants, `PLANTED_COLUMN` and `PBO_ALWAYS`.
`test_the_families_are_c2365ee_s_under_the_ids_gate_calibration_lists` pins them: every family's
spec to their SHA-256 at c2365ee, each of the 23 registered families parameter by parameter, and
the constants. The seed is kept so that the rerun pairs ledger for ledger with the `prereg-v1`
run; with the gate fixed, it is no degree of freedom. Rebasing #66 onto main after #65 gave its
three commits new hashes (8e89a02, 9ac814f and c2365ee became 5cd0c89, 515be05 and d888443)
without changing a byte of the study; the pin is to c2365ee's content.

**What had been seen before the rerun.** The full `prereg-v1` run of every family under the
pooled V; section 12's analytic figures under the null V; and the quant role's reduced check of
the revision (normal returns only, DEV-4 and DEV-5 taken as met), which found false passes in at
most 0.14% of no-skill ledgers and a single planted 2.0 passing in 88% to 94% (section 15, item
24). Before the first complete run of the study under the null V, the null-V code ran on the
study's own ledgers only in tests: ledger 0 of `null-w0.9-a0.9` and ledger 500 of
`single-H1-s1.5-high` in the equivalence test (neither hypothesis passes, and H1 passes, as under
the pooled V), and two ledgers each of `null-w0.99-a0.99` and `single-H2-s2-high` in the smoke
test, whose outcomes no test asserts or prints. The ledger on which the two forms of V disagree
was found in a test-only family, whose id seeds ledgers outside the study.

**The first complete run, seen before the rerun.** The study as committed in edd9286 ran in full
once, from 15:11:34 to 15:41:14 UTC on 2026-11-22, and its results and verdict were seen. Its
results file, SHA-256 `26efe37f0964a95141034f5fab20eed38fcb126ebe7d79df849fffb8faeaea40`, failed
one check before it could be committed: `test_the_committed_full_run_is_the_one_the_report_cites`
requires the report's results section to equal a rendering of the file, and the two listed the
four detection families in different orders. The run rendered the section from its results in
memory, in registered order, but the file is written with sorted keys, and the renderer listed the
families in the order of the dict it was given. Every figure was the same. The only change since
is that the renderer lists them in registered order, with
`test_the_results_section_survives_saving_and_reloading_the_results_file`; no family, seed,
threshold, simulation or line of the gate changed. The study is deterministic, so the rerun from
scratch at the commit holding this paragraph must reproduce that results file byte for byte; if it
did not, nothing would be committed. The run record under "Reproduce" says whether it did.

## What was calibrated

The gate as section 10 registers it under `prereg-v2` and `norse_research.gate` implements it
(G1.5 and G1.6, as merged in #56 and #59, with D21 and D22 as merged in #65): each hypothesis's
candidate is its selectable run with the highest DEV-1 z, ties to the earlier configuration; it
must meet DEV-1 to DEV-5; the holdout candidate is the passing hypothesis's candidate with the
higher z, ties to H1. N is the registered budget of 30 (and 14, every configuration run once, as
a sensitivity). V is the null variance 1/(T - 1) of the candidate's window for DEV-1, and of the
excess series' window for DEV-2 (decision D22): it reads no run, so a real edge does not raise
its own bar and neither hypothesis's bar depends on the other's runs. DEV-2 is volatility matched
for H1 and beta matched for H2 (D15). The PBO matrices have 8 and 6 columns over the windows of
section 3: H1 2,802 days from 2018-05-01, H2 2,185 days from 2020-01-08, both to 2025-12-31,
with calendar years for DEV-4 taken from those dates. A simulated ledger **passes** when it has
a holdout candidate, that is when at least one hypothesis passes development; that is the
decision that would open the holdout. HOLD-1 is reported separately as an end-to-end figure, on
304 further simulated days of the holdout candidate's own process.

**How the gate is called.** Every public function of `norse_research.gate` takes only a ledger the
gate itself issued (`gate_ledger`, or `_issue` in tests), so none can be called on a simulated
return matrix, and writing 54,000 hash-chained ledgers would test the ledger rather than the
statistics. The study therefore calls the statistics those functions are built from, on the same
inputs in the same order: `gate.daily_sharpe`, `gate._deflated` (the DSR and its z, the
zero-variance rule included), `sharpe.null_sharpe_variance` for V, as
`gate.trial_sharpe_variance` returns it, `gate.EXCESS_MATCH` with `benchmark.excess_returns` for
DEV-2, `cscv.cscv` with `GATE_BLOCKS` for DEV-3, `gate.sharpe_without_largest` for DEV-5 and
`gate.Development` for every threshold and inequality. Three pieces are written out because their
gate versions take the ledger: DEV-4 (`gate.sharpe_without_each_year`), the candidate rule
(`gate.candidates`) and the holdout candidate rule (`gate._holdout_candidate`), each with the
gate's own expression. To prove the composition is the gate,
`test_the_study_judges_a_ledger_exactly_as_the_gate_does` writes three simulated ledgers into
in-memory ledgers (all 14 configurations, both benchmarks and 16 failed reruns, so N is 30), runs
`gate.development` and `gate.holdout_candidate` on them, and requires the same candidate and bit
for bit the same DSR, z, excess DSR, PBO, DEV-4 and DEV-5 values and the same holdout candidate:
for a ledger where neither hypothesis passes, one where H1 does, and one, from a test-only family,
where H1 passes under the null V but would fail DEV-1 and DEV-2 under `prereg-v1`'s pooled V, so
that a stale V fails the test on the decision itself. The study also checks, on every ledger,
that the z it ranks by equals the z `gate._deflated` returns for the candidate.

HOLD-1 has no implementation in the package yet (G3.2), so `calibration.hold_1` writes section
10's formula out; the end-to-end figures rest on it and are reported, never judged.

## The simulated ledgers

Each ledger holds the 14 configurations' daily returns on the registered calendar (H1's window,
H2's inside it, then the 304 holdout days) and the two benchmarks' returns. Configuration j of
hypothesis h on day t returns

    r = SIGMA * ( alpha + l_h * b_h(t) + v(t) * (a * F(t) + b * G_h(t) + d * e_j(t)) ),

with b_h the standardised return of h's benchmark (its drift included, so a loading carries
market exposure without skill), F a factor common to both hypotheses, G_h a factor per
hypothesis and e_j one per configuration, all of unit variance. The loadings are solved from
the family's correlations: within a hypothesis l_h^2 + a^2 + b^2, across the two l_1 l_2 c + a^2
with c = 0.9 the correlation of B-H1 and B-H2 on common days, and l_h with the configuration's
own benchmark. SIGMA is 1% a day and the benchmarks' volatility 3%; every statistic in the gate is
scale free. alpha is 0 without skill and the planted annualised Sharpe ratio over the square root
of 365 where an edge is planted; with the benchmarks driftless, as in every power family, the
planted figure is both the configuration's Sharpe ratio and its alpha's. Shocks are normal, or
Student t with 5 degrees of freedom (kurtosis 9); v is 1, or a persistent volatility regime shared
by every series (log volatility AR(1) with coefficient 0.98 and standard deviation 0.4, scaled so
that E[v^2] = 1); in two sensitivity families every shock is AR(1) with coefficient 0.1. A test
checks the realised correlations, volatilities, shock shapes and planted means against these
targets.

**The families.** 54 families of 1,000 ledgers each.

The 17 **no-skill families** are the ones the no-skill half rests on. Nine are what section 10
requires: normal, serially independent returns, benchmarks unrelated to the strategies and
without drift, correlated within each hypothesis at 0, 0.5, 0.9, 0.99 and 0.999 and across the
two from 0 up to the within value, including the near-identical families. Three give the
configurations market exposure without skill (H1 correlated 0.7 with B-H1, as a long and flat
rule is; H2 0.2 or 0.5 with B-H2) in a market with an annualised Sharpe ratio of 0.5 or 1.0, the
case DEV-2 exists for and the one that calibrates D15's beta-matched excess. Two are the power
families' layout with nothing planted. Three add fat tails and a volatility regime at
correlations 0, 0.9 and 0.99. Two **sensitivity families**, serially correlated returns, are
reported but not judged.

The 35 **power families** plant an edge in one hypothesis, the other without skill. H1's
configurations are correlated 0.5 with B-H1, so that their volatility-matched excess has exactly
the planted Sharpe ratio (section 12's formula (S - S_B) / sqrt(2 - 2 rho) with S_B = 0 and rho =
0.5); H2's are uncorrelated with B-H2, so that the beta-matched excess has it too. For each
hypothesis, a **single planted column** (the first registered configuration) or a **shared edge**
(every configuration of the hypothesis), at Sharpe ratios of 1.0, 1.5 and 2.0, with correlations
of 0.5 within and 0 across ("low") or 0.9 within and 0.5 across ("high"); a single column also at
2.5 and 3.0; and, for contrast, both hypotheses sharing one edge of the same size. The four
single-column families at 2.0 are the detection families.

**Seeds.** Ledger i of family f draws from Python's `random.Random("20261121/f/i")`, so any ledger
can be rebuilt alone (`calibration.simulate`). The study seed 20261121 was fixed before the first
run.

**What the study computes.** On every ledger: the outcome at N = 30 (registered) and at N = 14,
both under the registered null V, and, as a counterfactual that is not the gate, the outcome at
N = 30 under `prereg-v1`'s pooled V (`counterfactual_pooled_v`). With the seed and the families
unchanged, that counterfactual should reproduce the `prereg-v1` run's N = 30 figures exactly,
family by family, which checks that the simulation did not change. The PBO is computed for a
hypothesis whenever it can decide the outcome (its candidate meets every other criterion under
any of the three judgements) and always for the first 200 ledgers of each family, from which
DEV-3's own pass rate is reported; where it is not computed the hypothesis fails another
criterion anyway, so whether a ledger passes is exact for every ledger.

## Results of the rerun (`prereg-v2`)

<!-- results: begin. Generated from the results file by `python -m norse_research.calibration --report`; not edited by hand. -->
**The gate meets the restated clause** of section 10 ("Calibration") of [PREREGISTRATION.md](../PREREGISTRATION.md), as `gate.calibration` in [preregistration.json](../preregistration.json) pins it.

**No-skill half: met.** In the 17 no-skill families judged, the gate produced a holdout candidate at N = 30 in at most 2 of 1,000 ledgers, in `null-w0.5-a0` (95% interval 0.02 to 0.72%); the limit is 5% of the ledgers in every family.

**Detection half: met.** The planted hypothesis passed development at N = 30 in 917 (`single-H1-s2-low`), 940 (`single-H1-s2-high`), 880 (`single-H2-s2-low`) and 883 (`single-H2-s2-high`) of 1,000 ledgers. The minimum, 880 in `single-H2-s2-low`, decides, and the clause needs more than 50% of the ledgers in each.

Both halves hold, so section 10's calibration condition does not stop the gate judging a strategy.

Every figure in this section was generated from [gate-calibration-results.json](../../research/calibration/gate-calibration-results.json) by `calibration.results_markdown` and written here by the run itself; `test_the_committed_full_run_is_the_one_the_report_cites` regenerates it from the committed file and requires it to be identical. The file's SHA-256 is `26efe37f0964a95141034f5fab20eed38fcb126ebe7d79df849fffb8faeaea40`. It holds seed 20261121, 1,000 ledgers in each of 54 families, a SHA-256 of every family's per-ledger records and the SHA-256 of the registration the study read, `736d51810d666bffaa79bf4bf4e4ee6bede23ea6487feac6552df955d0cc5079`. Counts are of 1,000 ledgers; intervals are Clopper-Pearson at 95%. "Pooled V" is prereg-v1's V at N = 30, a counterfactual that is not the gate and that no verdict reads.

### No-skill families

"Passes" is the gate producing a holdout candidate. "Judged" marks the `no_skill_families` of `gate.calibration`; the others are reported only.

| Family | Judged | Within / across | Other | N = 30: passes | 95% interval | N = 14 | With HOLD-1 (N = 30) | Pooled V (N = 30) |
|---|---|---|---|---|---|---|---|---|
| `null-w0-a0` | yes | 0 / 0 | none | 0 | 0.00 to 0.37% | 2 | 0 | 0 |
| `null-w0.5-a0` | yes | 0.5 / 0 | none | 2 | 0.02 to 0.72% | 4 | 0 | 0 |
| `null-w0.5-a0.5` | yes | 0.5 / 0.5 | none | 0 | 0.00 to 0.37% | 1 | 0 | 2 |
| `null-w0.9-a0` | yes | 0.9 / 0 | none | 0 | 0.00 to 0.37% | 2 | 0 | 3 |
| `null-w0.9-a0.5` | yes | 0.9 / 0.5 | none | 0 | 0.00 to 0.37% | 1 | 0 | 2 |
| `null-w0.9-a0.9` | yes | 0.9 / 0.9 | none | 0 | 0.00 to 0.37% | 1 | 0 | 5 |
| `null-w0.99-a0.9` | yes | 0.99 / 0.9 | none | 0 | 0.00 to 0.37% | 0 | 0 | 8 |
| `null-w0.99-a0.99` | yes | 0.99 / 0.99 | none | 0 | 0.00 to 0.37% | 0 | 0 | 6 |
| `null-w0.999-a0.999` | yes | 0.999 / 0.999 | none | 0 | 0.00 to 0.37% | 0 | 0 | 6 |
| `exposure-sb0.5` | yes | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.2 (H2); benchmark Sharpe 0.5 | 0 | 0.00 to 0.37% | 1 | 0 | 1 |
| `exposure-sb1` | yes | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.2 (H2); benchmark Sharpe 1 | 0 | 0.00 to 0.37% | 0 | 0 | 1 |
| `exposure-sb1-h2beta0.5` | yes | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.5 (H2); benchmark Sharpe 1 | 0 | 0.00 to 0.37% | 1 | 0 | 0 |
| `null-power-layout-low` | yes | 0.5 / 0 | benchmark correlation 0.5 (H1), 0 (H2) | 0 | 0.00 to 0.37% | 1 | 0 | 0 |
| `null-power-layout-high` | yes | 0.9 / 0.5 | benchmark correlation 0.5 (H1), 0 (H2) | 0 | 0.00 to 0.37% | 0 | 0 | 2 |
| `fat-w0-a0` | yes | 0 / 0 | Student t(5) shocks; volatility regime | 1 | 0.00 to 0.56% | 1 | 0 | 0 |
| `fat-w0.9-a0.9` | yes | 0.9 / 0.9 | Student t(5) shocks; volatility regime | 0 | 0.00 to 0.37% | 1 | 0 | 9 |
| `fat-w0.99-a0.99` | yes | 0.99 / 0.99 | Student t(5) shocks; volatility regime | 0 | 0.00 to 0.37% | 1 | 0 | 13 |
| `ar0.1-w0-a0` | reported only | 0 / 0 | autocorrelation 0.1 | 1 | 0.00 to 0.56% | 9 | 0 | 0 |
| `ar0.1-w0.9-a0.9` | reported only | 0.9 / 0.9 | autocorrelation 0.1 | 0 | 0.00 to 0.37% | 3 | 0 | 8 |

Each criterion's no-skill pass rate at N = 30: the share of ledgers in which each hypothesis's candidate meets it (DEV-3 over the first 200 ledgers of each family, where the PBO is always computed; every other column over all of them), and the count passing all five.

| Family | H1: DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all | H2: DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `null-w0-a0` | 0.0% | 0.1% | 54.0% | 91.6% | 70.3% | 0 | 0.0% | 0.0% | 50.5% | 82.5% | 55.5% | 0 |
| `null-w0.5-a0` | 0.1% | 0.0% | 57.5% | 69.7% | 47.3% | 0 | 0.2% | 0.2% | 50.5% | 62.7% | 36.7% | 2 |
| `null-w0.5-a0.5` | 0.0% | 0.0% | 52.0% | 68.3% | 46.9% | 0 | 0.0% | 0.0% | 46.0% | 61.6% | 37.8% | 0 |
| `null-w0.9-a0` | 0.0% | 0.0% | 53.5% | 46.6% | 27.9% | 0 | 0.0% | 0.0% | 43.0% | 42.9% | 21.7% | 0 |
| `null-w0.9-a0.5` | 0.1% | 0.0% | 47.5% | 43.4% | 25.3% | 0 | 0.0% | 0.0% | 41.5% | 41.1% | 20.9% | 0 |
| `null-w0.9-a0.9` | 0.0% | 0.0% | 54.0% | 44.2% | 24.9% | 0 | 0.0% | 0.0% | 45.5% | 40.3% | 19.7% | 0 |
| `null-w0.99-a0.9` | 0.1% | 0.0% | 48.5% | 33.1% | 17.3% | 0 | 0.0% | 0.0% | 46.5% | 31.5% | 15.8% | 0 |
| `null-w0.99-a0.99` | 0.0% | 0.0% | 56.5% | 35.9% | 17.6% | 0 | 0.0% | 0.0% | 54.0% | 32.0% | 13.1% | 0 |
| `null-w0.999-a0.999` | 0.0% | 0.1% | 47.5% | 30.2% | 15.8% | 0 | 0.0% | 0.0% | 48.5% | 30.5% | 13.6% | 0 |
| `exposure-sb0.5` | 1.0% | 0.0% | 50.0% | 81.6% | 62.8% | 0 | 0.0% | 0.0% | 46.5% | 52.9% | 29.5% | 0 |
| `exposure-sb1` | 8.5% | 0.0% | 51.0% | 97.4% | 92.4% | 0 | 0.3% | 0.0% | 56.0% | 61.2% | 39.4% | 0 |
| `exposure-sb1-h2beta0.5` | 10.5% | 0.0% | 53.0% | 97.3% | 92.6% | 0 | 1.4% | 0.0% | 50.0% | 86.2% | 70.0% | 0 |
| `null-power-layout-low` | 0.0% | 0.0% | 56.5% | 69.0% | 47.8% | 0 | 0.0% | 0.0% | 47.5% | 62.9% | 38.2% | 0 |
| `null-power-layout-high` | 0.0% | 0.1% | 44.0% | 46.4% | 26.6% | 0 | 0.0% | 0.0% | 49.0% | 41.8% | 21.5% | 0 |
| `fat-w0-a0` | 0.3% | 0.1% | 50.0% | 89.8% | 33.1% | 0 | 0.1% | 0.1% | 50.0% | 82.5% | 21.6% | 1 |
| `fat-w0.9-a0.9` | 0.0% | 0.0% | 48.5% | 46.9% | 11.4% | 0 | 0.0% | 0.0% | 51.5% | 41.7% | 10.7% | 0 |
| `fat-w0.99-a0.99` | 0.1% | 0.1% | 54.0% | 35.3% | 6.5% | 0 | 0.0% | 0.0% | 53.0% | 32.8% | 5.9% | 0 |
| `ar0.1-w0-a0` | 0.6% | 0.3% | 56.5% | 90.5% | 77.6% | 1 | 0.1% | 0.0% | 45.5% | 82.9% | 62.1% | 0 |
| `ar0.1-w0.9-a0.9` | 0.1% | 0.0% | 52.0% | 43.4% | 29.3% | 0 | 0.0% | 0.0% | 48.5% | 39.2% | 22.6% | 0 |

### Power

The planted hypothesis passing development. "Judged" marks the four `detection_families` of `gate.calibration`; every other row is reported only. "Planted column" counts the passes whose candidate was the planted configuration (single-column families only). "With HOLD-1" also requires the holdout candidate to be in the planted hypothesis and HOLD-1 to hold on its 304 further days.

| Hypothesis | Form | Sharpe | Within / across | Judged | N = 30: passes | 95% interval | Planted column | N = 14 | Pooled V (N = 30) | With HOLD-1 (N = 30) |
|---|---|---|---|---|---|---|---|---|---|---|
| H1 | single | 1 | 0.5 / 0 | reported only | 63 | 4.87 to 7.99% | 63 | 141 | 10 | 53 |
| H1 | single | 1.5 | 0.5 / 0 | reported only | 499 | 46.76 to 53.05% | 499 | 657 | 144 | 445 |
| H1 | single | 2 | 0.5 / 0 | yes | 917 | 89.81 to 93.34% | 917 | 967 | 512 | 862 |
| H1 | single | 2.5 | 0.5 / 0 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 870 | 949 |
| H1 | single | 3 | 0.5 / 0 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 979 | 935 |
| H1 | single | 1 | 0.9 / 0.5 | reported only | 78 | 6.21 to 9.64% | 78 | 154 | 128 | 62 |
| H1 | single | 1.5 | 0.9 / 0.5 | reported only | 524 | 49.25 to 55.53% | 524 | 661 | 335 | 468 |
| H1 | single | 2 | 0.9 / 0.5 | yes | 940 | 92.34 to 95.39% | 940 | 971 | 640 | 882 |
| H1 | single | 2.5 | 0.9 / 0.5 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 840 | 931 |
| H1 | single | 3 | 0.9 / 0.5 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 962 | 950 |
| H2 | single | 1 | 0.5 / 0 | reported only | 91 | 7.39 to 11.06% | 91 | 163 | 15 | 75 |
| H2 | single | 1.5 | 0.5 / 0 | reported only | 468 | 43.67 to 49.95% | 468 | 609 | 171 | 414 |
| H2 | single | 2 | 0.5 / 0 | yes | 880 | 85.82 to 89.95% | 880 | 930 | 501 | 815 |
| H2 | single | 2.5 | 0.5 / 0 | reported only | 993 | 98.56 to 99.72% | 993 | 999 | 850 | 934 |
| H2 | single | 3 | 0.5 / 0 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 971 | 947 |
| H2 | single | 1 | 0.9 / 0.5 | reported only | 94 | 7.66 to 11.38% | 94 | 164 | 91 | 75 |
| H2 | single | 1.5 | 0.9 / 0.5 | reported only | 466 | 43.47 to 49.75% | 466 | 618 | 315 | 420 |
| H2 | single | 2 | 0.9 / 0.5 | yes | 883 | 86.14 to 90.23% | 883 | 947 | 626 | 825 |
| H2 | single | 2.5 | 0.9 / 0.5 | reported only | 990 | 98.17 to 99.52% | 990 | 997 | 843 | 916 |
| H2 | single | 3 | 0.9 / 0.5 | reported only | 1,000 | 99.63 to 100.00% | 1,000 | 1,000 | 948 | 931 |
| H1 | shared | 1 | 0.5 / 0 | reported only | 226 | 20.04 to 25.32% | n/a | 327 | 46 | 182 |
| H1 | shared | 1.5 | 0.5 / 0 | reported only | 476 | 44.47 to 50.75% | n/a | 486 | 79 | 419 |
| H1 | shared | 2 | 0.5 / 0 | reported only | 510 | 47.85 to 54.14% | n/a | 510 | 80 | 450 |
| H1 | shared | 1 | 0.9 / 0.5 | reported only | 84 | 6.76 to 10.29% | n/a | 130 | 46 | 68 |
| H1 | shared | 1.5 | 0.9 / 0.5 | reported only | 382 | 35.18 to 41.29% | n/a | 436 | 44 | 347 |
| H1 | shared | 2 | 0.9 / 0.5 | reported only | 499 | 46.76 to 53.05% | n/a | 501 | 50 | 454 |
| H2 | shared | 1 | 0.5 / 0 | reported only | 218 | 19.28 to 24.49% | n/a | 296 | 6 | 173 |
| H2 | shared | 1.5 | 0.5 / 0 | reported only | 483 | 45.16 to 51.45% | n/a | 504 | 8 | 407 |
| H2 | shared | 2 | 0.5 / 0 | reported only | 525 | 49.35 to 55.63% | n/a | 526 | 7 | 464 |
| H2 | shared | 1 | 0.9 / 0.5 | reported only | 92 | 7.48 to 11.16% | n/a | 150 | 4 | 79 |
| H2 | shared | 1.5 | 0.9 / 0.5 | reported only | 325 | 29.60 to 35.50% | n/a | 371 | 5 | 284 |
| H2 | shared | 2 | 0.9 / 0.5 | reported only | 478 | 44.66 to 50.95% | n/a | 488 | 0 | 423 |
| H1 and H2 | shared | 1 | 0.9 / 0.5 | reported only | 191 | 16.71 to 21.68% | n/a | 291 | 506 | 158 |
| H1 and H2 | shared | 1.5 | 0.9 / 0.5 | reported only | 592 | 56.08 to 62.27% | n/a | 650 | 690 | 519 |
| H1 and H2 | shared | 2 | 0.9 / 0.5 | reported only | 744 | 71.58 to 77.08% | n/a | 751 | 747 | 673 |

Which criterion binds, for each planted hypothesis at N = 30 (DEV-3 over the first 200 ledgers of each family):

| Family | Hypothesis | DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all |
|---|---|---|---|---|---|---|---|
| `single-H1-s1-low` | H1 | 16.3% | 17.4% | 99.5% | 98.7% | 97.1% | 6.3% |
| `single-H1-s1.5-low` | H1 | 64.8% | 66.2% | 100.0% | 100.0% | 99.9% | 49.9% |
| `single-H1-s2-low` | H1 | 95.9% | 94.9% | 100.0% | 100.0% | 100.0% | 91.7% |
| `single-H1-s2.5-low` | H1 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `single-H1-s3-low` | H1 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `single-H1-s1-high` | H1 | 16.9% | 19.5% | 100.0% | 98.1% | 95.7% | 7.8% |
| `single-H1-s1.5-high` | H1 | 66.8% | 66.2% | 100.0% | 100.0% | 99.9% | 52.4% |
| `single-H1-s2-high` | H1 | 96.7% | 96.3% | 100.0% | 100.0% | 100.0% | 94.0% |
| `single-H1-s2.5-high` | H1 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `single-H1-s3-high` | H1 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `single-H2-s1-low` | H2 | 9.4% | 9.3% | 98.0% | 96.7% | 90.7% | 9.1% |
| `single-H2-s1.5-low` | H2 | 47.1% | 46.9% | 100.0% | 99.7% | 99.1% | 46.8% |
| `single-H2-s2-low` | H2 | 88.0% | 88.0% | 100.0% | 100.0% | 100.0% | 88.0% |
| `single-H2-s2.5-low` | H2 | 99.4% | 99.3% | 100.0% | 100.0% | 100.0% | 99.3% |
| `single-H2-s3-low` | H2 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `single-H2-s1-high` | H2 | 9.4% | 9.5% | 100.0% | 96.0% | 89.8% | 9.4% |
| `single-H2-s1.5-high` | H2 | 47.0% | 47.1% | 100.0% | 99.7% | 99.3% | 46.6% |
| `single-H2-s2-high` | H2 | 88.3% | 88.3% | 100.0% | 100.0% | 99.9% | 88.3% |
| `single-H2-s2.5-high` | H2 | 99.0% | 99.0% | 100.0% | 100.0% | 100.0% | 99.0% |
| `single-H2-s3-high` | H2 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| `shared-H1-s1-low` | H1 | 52.1% | 53.6% | 52.0% | 100.0% | 100.0% | 22.6% |
| `shared-H1-s1.5-low` | H1 | 96.5% | 95.9% | 52.0% | 100.0% | 100.0% | 47.6% |
| `shared-H1-s2-low` | H1 | 100.0% | 100.0% | 52.5% | 100.0% | 100.0% | 51.0% |
| `shared-H1-s1-high` | H1 | 30.6% | 29.4% | 50.0% | 99.7% | 98.9% | 8.4% |
| `shared-H1-s1.5-high` | H1 | 81.8% | 79.8% | 51.0% | 100.0% | 99.9% | 38.2% |
| `shared-H1-s2-high` | H1 | 99.3% | 99.2% | 54.5% | 100.0% | 100.0% | 49.9% |
| `shared-H2-s1-low` | H2 | 32.8% | 32.7% | 49.5% | 100.0% | 99.5% | 21.8% |
| `shared-H2-s1.5-low` | H2 | 85.2% | 85.6% | 49.0% | 100.0% | 100.0% | 48.3% |
| `shared-H2-s2-low` | H2 | 99.5% | 99.5% | 51.5% | 100.0% | 100.0% | 52.5% |
| `shared-H2-s1-high` | H2 | 17.8% | 18.1% | 51.5% | 98.0% | 95.2% | 9.2% |
| `shared-H2-s1.5-high` | H2 | 65.4% | 65.4% | 46.0% | 100.0% | 100.0% | 32.5% |
| `shared-H2-s2-high` | H2 | 94.1% | 94.1% | 56.0% | 100.0% | 100.0% | 47.8% |
| `shared-both-s1-high` | H1 | 31.1% | 30.5% | 47.0% | 99.7% | 99.0% | 10.4% |
| `shared-both-s1-high` | H2 | 19.0% | 18.9% | 52.5% | 99.3% | 96.1% | 11.6% |
| `shared-both-s1.5-high` | H1 | 83.2% | 83.5% | 51.5% | 100.0% | 100.0% | 39.6% |
| `shared-both-s1.5-high` | H2 | 66.6% | 67.1% | 49.0% | 99.9% | 99.8% | 34.6% |
| `shared-both-s2-high` | H1 | 99.4% | 99.2% | 49.0% | 100.0% | 100.0% | 48.7% |
| `shared-both-s2-high` | H2 | 94.6% | 94.6% | 53.5% | 100.0% | 100.0% | 49.0% |
<!-- results: end -->

## History: the `prereg-v1` run (2026-11-21)

This is the report of the first full run as it was committed in c2365ee (now d888443), kept as
disclosed history. Only its headings, the link to its results file under that file's new name
and one figure have changed: section 12's four-in-five Sharpe ratio for H2 at N = 30 reads 1.87
in two places where it read 1.86, as section 12 of PREREGISTRATION.md was corrected in
`prereg-v2`. It describes the gate under `prereg-v1`: where it says "the registered V" or "the
gate", read `prereg-v1`'s pooled V. Its "Own V" column is the counterfactual this rerun dropped.
Its findings led to decision D22.

### Verdict

**The gate does not pass calibration as section 10 of
[PREREGISTRATION.md](../PREREGISTRATION.md) words it. It is safe, but it is weak.** The verdict is
taken against the definitions in "Readings fixed before the full run" below, which were committed
in 9ac814f and pushed at 21:35 UTC on 2026-11-21, while the full run was still computing; its
results file was written at 22:22 UTC.

**The no-skill half is met.** In none of the 17 no-skill families did the gate pass more than 13 of
1,000 ledgers at N = 30; the worst is `fat-w0.99-a0.99` (configurations correlated 0.99 within and
across the hypotheses, fat tails and a volatility regime) at 1.3%, 95% interval 0.69% to 2.21%. The
worst normal family passes 0.8%. At N = 14 the worst is 1.4%, and with HOLD-1 applied as well
never more than 0.5%. Every count is well inside the 50 of 1,000 allowed.

**The detection half is not met.** The definition asks that a single planted configuration with an
annualised Sharpe ratio of 1.5 pass development in more than 500 of 1,000 ledgers in each of four
families. It passes in 144, 335, 171 and 315 (H1 at low and high correlation, then H2), so the
minimum is 144, and none of the four clears 500. A single planted configuration reaches even odds
only at a Sharpe ratio of about 2.0 (501 to 640 of 1,000) and four times in five at about 2.5 (840
to 870). A hypothesis whose configurations all share one edge, while the other hypothesis has none,
is almost never passed at any size tried: 44 to 80 of 1,000 for H1 and 0 to 8 for H2 at Sharpe ratios
of 1.0, 1.5 and 2.0, barely rising with the edge's size. The cause is in the registered gate, not in the
code: V is pooled across both hypotheses, so a real difference between H1 and H2 is charged as if it
were the dispersion of skill-less trials (finding 1).

**What follows.** Section 10 makes both halves a condition of judging any strategy ("no strategy is
judged until it passes at most 5% of 1,000 simulated no-skill ledgers at N = 30 and detects a planted
Sharpe of 1.5"), so as registered no strategy is judged until the owner decides what to do about the
detection half. The kill criterion in [NEXT_ITERATION.md](../NEXT_ITERATION.md) names only the
no-skill half, which is met, so the two documents disagree on the consequence (finding 4). A gate
that rarely passes skill-less ledgers is safe to apply; what a weak one costs is that its FAIL means
little, since a strategy with a true Sharpe ratio of 1.5 would most likely be failed. The decision is
still open in time: change control under section 16 closes only at the first ledger entry after the
genesis entry, and the genesis entry has not been written, so a `prereg-v2` is possible before any
trial. Any change to the gate needs its own calibration run.

### Readings fixed before the full run

The registration leaves four things open. Each reading below was written into the code before the
full run, and none of them moves a pass mark.

1. **What "passes" means** (section 10, "Calibration"). A no-skill ledger passes when the gate
   produces a holdout candidate, which is the full development gate including the candidate
   rule. HOLD-1 is excluded from the judged rate because the holdout is contaminated (D4) and
   because excluding it can only raise the false-pass rate; with HOLD-1 the rates are lower still
   and are reported.
2. **"At most 5%"** (section 10). At most 50 of the 1,000 ledgers, as worded, in every no-skill
   family separately, not on average across them. The Clopper-Pearson 95% interval is reported
   beside each count.
3. **"Detects a planted Sharpe of 1.5"** (section 10; NEXT_ITERATION.md, "The gate"). Neither
   document gives a power, so this is the definition, exactly:

   > The gate detects a planted Sharpe ratio of 1.5 if and only if, in each of the four
   > single-planted-column families at an annualised Sharpe ratio of 1.5
   > (`single-H1-s1.5-low`, `single-H1-s1.5-high`, `single-H2-s1.5-low` and
   > `single-H2-s1.5-high`), the planted hypothesis passes development (its candidate meets
   > DEV-1 to DEV-5 at N = 30) in more than 500 of the 1,000 ledgers. Every one of the four
   > must clear 500; the minimum decides.

   In the code this is `DETECTION_POWER = 0.5`, `DETECTION_SHARPE = 1.5` and
   `calibration.verdict`, which computes it from the committed results and which a test
   recomputes from them.

   The reasons, from the registration. *The bar of one half:* section 12 defines every pass mark
   as "the true Sharpe ratio with even odds of passing", so "detects" read in the registration's
   own terms means more likely than not to pass. Four fifths, the usual power in a study design,
   would make the calibration fail by the registration's own arithmetic before any simulation:
   section 12 already puts the Sharpe ratio DEV-1 alone passes four times in five at 1.65 (H1) and
   1.87 (H2) at N = 30, above 1.5. One half is not generous either: section 12's even-odds DEV-1
   mark for H2 at N = 30 is 1.52, so H2 at 1.5 sits at about even odds on DEV-1 alone before DEV-2
   to DEV-5 and the registered V are applied. *A single planted column:* section 10 says the gate
   must "detect a planted Sharpe of 1.5" and, separately, that "power must be reported for a family
   whose configurations share one edge as well as for a single planted column"; detection is
   singular ("a planted Sharpe"), as in G1.3's planted-edge checks, and the shared form is named
   only as something to report. So the shared-edge power is reported in full and is not part of the
   definition. *Each hypothesis and both correlation settings:* the gate judges each hypothesis on
   its own window (2,802 and 2,185 days), and a definition met by H1 alone would let the claim
   rest on the easier window; the two correlation settings are the two the power families use,
   and the minimum is taken so that the claim does not depend on picking the kinder one.
   *"Passes development" rather than "is the holdout candidate":* detection is a property of the
   gate's judgement of the planted hypothesis; whether the other hypothesis also passes by chance
   is a different question, and its figure is reported beside it.

4. **Which families are judged.** The 17 no-skill families above, at N = 30. The serially
   correlated families and N = 14 are sensitivities, reported because a reader should know them.

**When these were fixed, and what had been seen.** Readings 1 to 4 were written into the code
(`DETECTION_POWER`, `DETECTION_SHARPE`, `FALSE_PASS_LIMIT` and `verdict`) before any ledger was
simulated. A pilot of 20 ledgers in each of the first 46 families was then run, to time the study,
and its counts were looked at: so the direction of the results at 20 ledgers a family had been
seen before the code was first committed (8e89a02) and before this definition was written out in
prose. Git cannot show that the code's definition predates the pilot, since that code was
uncommitted when the pilot ran; what git does show is that the definition was committed, in the
code (8e89a02) and in this section (9ac814f), while the full run was still
computing and before its results file existed. After the pilot the only change was to add the
single-column families at Sharpe ratios of 2.5 and 3.0, to locate where power reaches one half;
no judged family, reading, seed, threshold or line of the gate was changed. The full run's
results are reported against this definition as it stands, whatever they show.

### Results

Every figure below is from
[gate-calibration-results-prereg-v1.json](../../research/calibration/gate-calibration-results-prereg-v1.json),
whose SHA-256 is `2eb307f3da3b425a036bf711207e2670c9641381e1a369d85094ad70037c4d9e`; the tables
were generated from it, not typed. Counts are of 1,000 ledgers; intervals are Clopper-Pearson at
95%.

#### No-skill ledgers

"Passes" is the gate producing a holdout candidate. "Within / across" are the correlations of two
configurations of one hypothesis and of an H1 and an H2 configuration.

| Family | Within / across | Other | N = 30: passes of 1,000 | 95% interval | N = 14 | With HOLD-1 (N = 30) |
|---|---|---|---|---|---|---|
| `null-w0-a0` | 0 / 0 | none | 0 | 0.00 to 0.37% | 0 | 0 |
| `null-w0.5-a0` | 0.5 / 0 | none | 0 | 0.00 to 0.37% | 3 | 0 |
| `null-w0.5-a0.5` | 0.5 / 0.5 | none | 2 | 0.02 to 0.72% | 6 | 1 |
| `null-w0.9-a0` | 0.9 / 0 | none | 3 | 0.06 to 0.87% | 3 | 2 |
| `null-w0.9-a0.5` | 0.9 / 0.5 | none | 2 | 0.02 to 0.72% | 5 | 1 |
| `null-w0.9-a0.9` | 0.9 / 0.9 | none | 5 | 0.16 to 1.16% | 7 | 4 |
| `null-w0.99-a0.9` | 0.99 / 0.9 | none | 8 | 0.35 to 1.57% | 9 | 3 |
| `null-w0.99-a0.99` | 0.99 / 0.99 | none | 6 | 0.22 to 1.30% | 7 | 5 |
| `null-w0.999-a0.999` | 0.999 / 0.999 | none | 6 | 0.22 to 1.30% | 7 | 2 |
| `exposure-sb0.5` | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.2 (H2); benchmark Sharpe 0.5 | 1 | 0.00 to 0.56% | 3 | 1 |
| `exposure-sb1` | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.2 (H2); benchmark Sharpe 1 | 1 | 0.00 to 0.56% | 1 | 1 |
| `exposure-sb1-h2beta0.5` | 0.9 / 0.5 | benchmark correlation 0.7 (H1), 0.5 (H2); benchmark Sharpe 1 | 0 | 0.00 to 0.37% | 0 | 0 |
| `null-power-layout-low` | 0.5 / 0 | benchmark correlation 0.5 (H1), 0 (H2) | 0 | 0.00 to 0.37% | 0 | 0 |
| `null-power-layout-high` | 0.9 / 0.5 | benchmark correlation 0.5 (H1), 0 (H2) | 2 | 0.02 to 0.72% | 4 | 1 |
| `fat-w0-a0` | 0 / 0 | Student t(5) shocks, volatility regime | 0 | 0.00 to 0.37% | 1 | 0 |
| `fat-w0.9-a0.9` | 0.9 / 0.9 | Student t(5) shocks, volatility regime | 9 | 0.41 to 1.70% | 10 | 4 |
| `fat-w0.99-a0.99` | 0.99 / 0.99 | Student t(5) shocks, volatility regime | 13 | 0.69 to 2.21% | 14 | 5 |
| `ar0.1-w0-a0` | 0 / 0 | autocorrelation 0.1; reported only | 0 | 0.00 to 0.37% | 0 | 0 |
| `ar0.1-w0.9-a0.9` | 0.9 / 0.9 | autocorrelation 0.1; reported only | 8 | 0.35 to 1.57% | 11 | 4 |

Each criterion's own no-skill pass rate, as G1.3's finding 3 asked: the share of ledgers in which
each hypothesis's candidate meets it at N = 30 (DEV-3 over the first 200 ledgers of each family,
where it is always computed; every other column over all 1,000), and the count passing all five.

| Family | H1: DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all | H2: DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `null-w0-a0` | 0.0% | 0.0% | 54.0% | 91.6% | 70.3% | 0 | 0.0% | 0.0% | 50.5% | 82.5% | 55.5% | 0 |
| `null-w0.5-a0` | 0.1% | 0.0% | 57.5% | 69.7% | 47.3% | 0 | 0.0% | 0.0% | 50.5% | 62.7% | 36.7% | 0 |
| `null-w0.5-a0.5` | 0.3% | 0.1% | 52.0% | 68.3% | 46.9% | 1 | 0.3% | 0.2% | 46.0% | 61.6% | 37.8% | 1 |
| `null-w0.9-a0` | 0.5% | 0.4% | 53.5% | 46.6% | 27.9% | 3 | 0.1% | 0.3% | 43.0% | 42.9% | 21.7% | 0 |
| `null-w0.9-a0.5` | 0.9% | 0.6% | 47.5% | 43.4% | 25.3% | 1 | 0.6% | 0.6% | 41.5% | 41.1% | 20.9% | 1 |
| `null-w0.9-a0.9` | 1.1% | 0.9% | 54.0% | 44.2% | 24.9% | 3 | 1.0% | 0.7% | 45.5% | 40.3% | 19.7% | 4 |
| `null-w0.99-a0.9` | 2.6% | 1.3% | 48.5% | 33.1% | 17.3% | 6 | 1.7% | 0.9% | 46.5% | 31.5% | 15.8% | 6 |
| `null-w0.99-a0.99` | 2.8% | 1.2% | 56.5% | 35.9% | 17.6% | 2 | 2.5% | 1.0% | 54.0% | 32.0% | 13.1% | 5 |
| `null-w0.999-a0.999` | 2.7% | 1.4% | 47.5% | 30.2% | 15.8% | 5 | 1.8% | 0.8% | 48.5% | 30.5% | 13.6% | 1 |
| `exposure-sb0.5` | 5.6% | 0.4% | 50.0% | 81.6% | 62.8% | 0 | 2.9% | 0.1% | 46.5% | 52.9% | 29.5% | 1 |
| `exposure-sb1` | 17.1% | 0.1% | 51.0% | 97.4% | 92.4% | 1 | 5.3% | 0.0% | 56.0% | 61.2% | 39.4% | 0 |
| `exposure-sb1-h2beta0.5` | 34.7% | 0.0% | 53.0% | 97.3% | 92.6% | 0 | 20.0% | 0.0% | 50.0% | 86.2% | 70.0% | 0 |
| `null-power-layout-low` | 0.0% | 0.0% | 56.5% | 69.0% | 47.8% | 0 | 0.0% | 0.0% | 47.5% | 62.9% | 38.2% | 0 |
| `null-power-layout-high` | 1.2% | 0.7% | 44.0% | 46.4% | 26.6% | 2 | 0.9% | 0.2% | 49.0% | 41.8% | 21.5% | 0 |
| `fat-w0-a0` | 0.0% | 0.0% | 50.0% | 89.8% | 33.1% | 0 | 0.0% | 0.0% | 50.0% | 82.6% | 21.6% | 0 |
| `fat-w0.9-a0.9` | 2.7% | 1.0% | 48.5% | 46.9% | 11.4% | 5 | 2.1% | 1.0% | 51.5% | 41.8% | 10.7% | 4 |
| `fat-w0.99-a0.99` | 3.0% | 1.5% | 54.0% | 35.3% | 6.5% | 8 | 2.6% | 1.7% | 53.0% | 32.8% | 5.9% | 9 |
| `ar0.1-w0-a0` | 0.0% | 0.1% | 56.5% | 90.5% | 77.6% | 0 | 0.0% | 0.1% | 45.5% | 82.9% | 62.1% | 0 |
| `ar0.1-w0.9-a0.9` | 2.3% | 1.1% | 52.0% | 43.4% | 29.3% | 2 | 2.6% | 1.6% | 48.5% | 39.2% | 22.6% | 6 |

The deflated Sharpe criteria carry the gate, as section 12 intended: DEV-2 is met by a skill-less
candidate in at most 1.7% of ledgers, and DEV-1, outside the families with market exposure, in at
most 3.0%, the highest where the configurations are almost identical and the registered V is
smallest. With market exposure DEV-1 alone passes up to 34.7% (H1) and 20.0% (H2), and DEV-2 stops
it. The PBO alone passes about half of all
skill-less hypotheses (41.5% to 57.5%), as G1.3 and section 13 said. DEV-4 and DEV-5 are met by a
skill-less candidate far more often than not when its configurations are independent, because the
candidate is the best of eight or six; they bind only together with the others.

#### Power

The share of ledgers in which the planted hypothesis passes development at N = 30, the registered
gate. Every single-column pass in the table had the planted configuration as its candidate. "Own V"
is the counterfactual, not registered, with V over each hypothesis's own runs (finding 1); "with
HOLD-1" also requires the holdout candidate to be in the planted hypothesis and HOLD-1 to hold on its
304 further days.

| Hypothesis | Form | Sharpe | Within / across | N = 30: passes of 1,000 | 95% interval | N = 14 | Own V | With HOLD-1 |
|---|---|---|---|---|---|---|---|---|
| H1 | single | 1 | 0.5 / 0 | 10 | 0.48 to 1.83% | 50 | 5 | 8 |
| H1 | single | 1.5 | 0.5 / 0 | 144 | 12.28 to 16.73% | 362 | 33 | 132 |
| H1 | single | 2 | 0.5 / 0 | 512 | 48.05 to 54.34% | 767 | 123 | 476 |
| H1 | single | 2.5 | 0.5 / 0 | 870 | 84.76 to 89.02% | 974 | 312 | 823 |
| H1 | single | 3 | 0.5 / 0 | 979 | 96.81 to 98.70% | 999 | 531 | 915 |
| H1 | single | 1 | 0.9 / 0.5 | 128 | 10.79 to 15.03% | 196 | 62 | 100 |
| H1 | single | 1.5 | 0.9 / 0.5 | 335 | 30.58 to 36.52% | 505 | 108 | 296 |
| H1 | single | 2 | 0.9 / 0.5 | 640 | 60.94 to 66.98% | 810 | 265 | 596 |
| H1 | single | 2.5 | 0.9 / 0.5 | 840 | 81.58 to 86.22% | 961 | 366 | 780 |
| H1 | single | 3 | 0.9 / 0.5 | 962 | 94.82 to 97.30% | 997 | 548 | 913 |
| H2 | single | 1 | 0.5 / 0 | 15 | 0.84 to 2.46% | 63 | 22 | 13 |
| H2 | single | 1.5 | 0.5 / 0 | 171 | 14.82 to 19.58% | 367 | 40 | 148 |
| H2 | single | 2 | 0.5 / 0 | 501 | 46.95 to 53.24% | 775 | 66 | 457 |
| H2 | single | 2.5 | 0.5 / 0 | 850 | 82.63 to 87.16% | 962 | 120 | 793 |
| H2 | single | 3 | 0.5 / 0 | 971 | 95.86 to 98.05% | 995 | 188 | 918 |
| H2 | single | 1 | 0.9 / 0.5 | 91 | 7.39 to 11.06% | 178 | 84 | 73 |
| H2 | single | 1.5 | 0.9 / 0.5 | 315 | 28.63 to 34.48% | 510 | 99 | 280 |
| H2 | single | 2 | 0.9 / 0.5 | 626 | 59.52 to 65.61% | 796 | 171 | 579 |
| H2 | single | 2.5 | 0.9 / 0.5 | 843 | 81.89 to 86.50% | 953 | 238 | 777 |
| H2 | single | 3 | 0.9 / 0.5 | 948 | 93.24 to 96.09% | 993 | 285 | 881 |
| H1 | shared | 1 | 0.5 / 0 | 46 | 3.39 to 6.09% | 114 | 350 | 36 |
| H1 | shared | 1.5 | 0.5 / 0 | 79 | 6.30 to 9.75% | 192 | 488 | 72 |
| H1 | shared | 2 | 0.5 / 0 | 80 | 6.39 to 9.86% | 252 | 510 | 68 |
| H1 | shared | 1 | 0.9 / 0.5 | 46 | 3.39 to 6.09% | 76 | 335 | 37 |
| H1 | shared | 1.5 | 0.9 / 0.5 | 44 | 3.21 to 5.86% | 116 | 507 | 40 |
| H1 | shared | 2 | 0.9 / 0.5 | 50 | 3.73 to 6.54% | 159 | 504 | 47 |
| H2 | shared | 1 | 0.5 / 0 | 6 | 0.22 to 1.30% | 32 | 325 | 5 |
| H2 | shared | 1.5 | 0.5 / 0 | 8 | 0.35 to 1.57% | 78 | 500 | 8 |
| H2 | shared | 2 | 0.5 / 0 | 7 | 0.28 to 1.44% | 122 | 526 | 6 |
| H2 | shared | 1 | 0.9 / 0.5 | 4 | 0.11 to 1.02% | 17 | 365 | 4 |
| H2 | shared | 1.5 | 0.9 / 0.5 | 5 | 0.16 to 1.16% | 34 | 471 | 4 |
| H2 | shared | 2 | 0.9 / 0.5 | 0 | 0.00 to 0.37% | 53 | 501 | 0 |
| H1 and H2 | shared | 1 | 0.9 / 0.5 | 506 | 47.45 to 53.74% | 549 | 599 | 415 |
| H1 and H2 | shared | 1.5 | 0.9 / 0.5 | 690 | 66.03 to 71.86% | 708 | 745 | 609 |
| H1 and H2 | shared | 2 | 0.9 / 0.5 | 747 | 71.89 to 77.37% | 751 | 757 | 675 |

Which criterion binds, for the planted hypothesis at N = 30 (DEV-3 over the first 200 ledgers):

| Family | DEV-1 | DEV-2 | DEV-3 | DEV-4 | DEV-5 | all |
|---|---|---|---|---|---|---|
| `single-H1-s1.5-low` | 28.9% | 30.2% | 100.0% | 100.0% | 99.9% | 14.4% |
| `single-H1-s1.5-high` | 45.7% | 47.8% | 100.0% | 100.0% | 99.9% | 33.5% |
| `single-H2-s1.5-low` | 22.2% | 22.9% | 100.0% | 99.7% | 99.1% | 17.1% |
| `single-H2-s1.5-high` | 38.6% | 37.3% | 100.0% | 99.7% | 99.3% | 31.5% |
| `single-H1-s2-high` | 75.4% | 73.7% | 100.0% | 100.0% | 100.0% | 64.0% |
| `single-H2-s2-high` | 66.7% | 66.8% | 100.0% | 100.0% | 99.9% | 62.6% |
| `shared-H1-s1.5-low` | 10.4% | 10.6% | 52.0% | 100.0% | 100.0% | 7.9% |
| `shared-H1-s1.5-high` | 8.1% | 8.3% | 51.0% | 100.0% | 99.9% | 4.4% |
| `shared-H2-s1.5-low` | 4.5% | 5.2% | 49.0% | 100.0% | 100.0% | 0.8% |
| `shared-H2-s1.5-high` | 4.2% | 4.4% | 46.0% | 100.0% | 100.0% | 0.5% |
| `shared-both-s1.5-high` | 93.6% | 94.0% | 51.5% | 100.0% | 100.0% | 46.5% |

The figures for every power family and criterion are in the results file.

### Why the gate is weak

The registered SR_0 is sqrt(V) times E[max Z_N], with E[max Z_30] = 2.0734 and V the sample variance
of all 14 selectable runs' Sharpe ratios. Take the noise away and look at what the edge alone does
to V (computed with `statistics.variance` and `minbtl.expected_max_z`, not by hand).

A **single planted configuration** with daily Sharpe ratio x among 13 at zero gives V = x^2 / 14,
so SR_0 = 0.554 x: the planted edge raises its own bar, and DEV-1 tests only the 45% of it that
is left. Even with no sampling noise among the other configurations, DEV-1 alone needs an annualised
1.33 for H1 and 1.51 for H2 for even odds; the 13 skill-less runs' own dispersion then adds to V,
DEV-2 must hold as well, and the gate's even-odds figure is about 2.0. Section 12's power figures hold V at 1/T, which is why they are more
hopeful than these (finding 2).

A **shared edge** in all eight H1 configurations with H2 at zero gives V = 0.264 x^2 (the same for
six H2 configurations against eight H1 at zero), so SR_0 = 1.065 x: the bar is above the edge
itself, the DSR is below one half whatever x is, and the pass rate does not rise with the edge's
size. The PBO is not what stops a shared edge, as section 13 expected; DEV-1 and DEV-2 stop it first.
At N = 14 the factor is 0.893 x, still close to the edge. The paper's V assumes the trials are
skill-less draws whose dispersion measures how much selection could find; a hypothesis that works
beside one that does not breaks that assumption, because the difference between them is real.

When both hypotheses share an edge of the same size, V is small again and power returns (506 to 747
of 1,000 at 1.0 to 2.0), now limited by DEV-3, which a family of equals passes about half the time.

### Findings

Each with severity (what goes wrong if ignored) and confidence. None is a defect in the code: the
gate computes what section 10 registers, bit for bit (the equivalence test).

1. **High, confirmed by simulation and algebra.** Because V pools both hypotheses, a hypothesis
   whose configurations share one edge, beside one without skill, is passed in at most 80 of 1,000
   ledgers (H1) and 8 of 1,000 (H2) at Sharpe ratios of 1.0 to 2.0, and the rate barely moves with
   the edge's size. Section 13 calls this the realistic case for H2's thresholds and H1's band and
   filter variants, and the most plausible success, one hypothesis working and the other not, is
   exactly this one. The counterfactual with V over each hypothesis's own runs restores 325 to 526
   of 1,000 (then limited by DEV-3) and keeps every no-skill family at or below 32 of 1,000, but cuts
   a single planted column's power at 1.5 to 33 to 108 of 1,000, because one outlier among eight or
   six inflates V more than among fourteen. Neither form of V serves both planted forms; any change
   is a registration change for the owner, with its own calibration.
2. **High, confirmed.** A single planted configuration at a Sharpe ratio of 1.5 is detected in 144 to
   335 of 1,000 ledgers, below the definition's 500 in all four families; power reaches one half at
   about 2.0 and four fifths at about 2.5. Section 12's statement that DEV-1 alone passes a true
   Sharpe ratio of 1.65 (H1) or 1.87 (H2) four times in five, and section 13's power figures, assume
   V = 1/T; under the registered V, DEV-1 alone passes a planted 2.0 in 56.4% to 75.4% of ledgers.
   Section 13's warning that a FAIL means "no edge large enough to detect" holds with a larger
   threshold than it states.
3. **Medium, confirmed.** The no-skill false-pass rate is far below 5% in every family, at most 1.3%
   at N = 30 and 1.4% at N = 14. It is highest where the configurations are near-identical within
   and across hypotheses, which is where the registered V is smallest, and fat tails with a volatility
   regime roughly double it there (13 against 6 of 1,000). The CTPO's earlier DEV-1-only figure of up
   to 3.5% (section 12) is consistent with the 2.8% and 3.0% DEV-1 rates here.
4. **Medium, confirmed.** Section 10 makes detection a condition of judging any strategy; the kill
   criterion in NEXT_ITERATION.md ("If the gate fails its no-skill calibration, no strategy is
   judged") names only the no-skill half. Under section 10 no strategy is judged until the owner
   decides; under NEXT_ITERATION.md alone the gate could judge. The pre-registration governs, so
   this needs the owner's decision, recorded as a decision and, if the gate or the condition changes,
   in a `prereg-v2` before the genesis entry.
5. **Medium, confirmed.** The register's open row "V in SR0 can be lowered by disqualifying a
   hypothesis" is material. If H2 loses its selectable runs (run rule 4: runs from different trees),
   H1's V is computed over its own runs only, which is the counterfactual above: a shared edge in H1
   goes from 44 to 80 passes to 335 to 510, and a skill-less H1 from at most 8 to at most 13. So the
   bar one hypothesis faces depends on whether the other has selectable runs, which a harness change
   that reruns only one hypothesis, or a deliberate tree mismatch, can decide.
6. **Medium, confirmed.** N matters more than its place in the formula suggests: at N = 14 a single
   planted 1.5 passes 362 to 510 of 1,000, against 144 to 335 at N = 30. Going from 14 entries to
   30, which one harness-wide rerun of all 14 configurations and two more entries would do, loses
   between a third and three fifths of the power at that size.
7. **Low, confirmed.** D15's beta-matched excess is calibrated and does its job. With H2 correlated
   0.5 with a benchmark of Sharpe ratio 1.0, H2's DEV-1 passes a skill-less candidate in 20.0% of
   ledgers on exposure alone and the beta-matched DEV-2 in 0.0%; where H2's edge is market neutral,
   DEV-2 passes about as often as DEV-1 (22.9% against 22.2% and 37.3% against 38.6% at 1.5), so the
   match costs no power. H1's volatility-matched DEV-2 likewise stops exposure (DEV-1 up to 34.7%,
   DEV-2 at most 0.4%).
8. **Low, confirmed.** The PBO passes a shared edge about half the time (46.0% to 56.0%), as
   section 13 said, but under the registered V it is not the criterion that binds (finding 1).
9. **Low, likely.** Positive serial correlation of 0.1 in every shock raises the most correlated
   family's rate from 5 to 8 of 1,000, within the limit. Stronger autocorrelation was not studied;
   the DSR's standard error ignores it.
10. **Low, confirmed.** The gate's public functions accept only a ledger the gate issued, so the study
    composes the gate from its statistics, calls the private `gate._deflated`, and writes out DEV-4
    and both candidate rules. The equivalence test guards against drift, but a public entry point in
    `gate.py` that judges a family of return series (with the ledger functions calling it) would let
    the gate and its calibration share one code path. Follow-up for the technical specialist.
11. **Low, confirmed.** HOLD-1 has no implementation yet (G3.2); the end-to-end figures rest on
    `calibration.hold_1`, written from section 10, and G3.2's implementation should be checked
    against it.
12. **Informational.** The definition of "detects" was fixed before the full run's results existed
    (9ac814f); a 20-ledger pilot had been seen before the code was first committed, as disclosed
    above.

### How it was run

The `prereg-v1` run took 7,085 seconds (about two hours) with 10 worker processes on an Apple M4
(10 cores, 24 GB) under macOS 26.5.2 and Python 3.13.11, from commit 8e89a02. The machine was
shared with other builds and the market-data recorder (load averages of 33 to 77), and the run was
reniced to 15 after about 35 minutes so as not to starve the recorder.

## What this calibration cannot show

The simulated configurations are a linear factor model with constant correlations and a constant
edge. Real configurations of one hypothesis differ in signal, an edge can come and go by regime, and
a real H1 holds a market whose drift sets how hard its volatility-matched DEV-2 is (section 12's
formula: the strategy needs S_B plus the DEV-1 figure times sqrt(2 - 2 rho)); every power family here
has a driftless benchmark and H1 correlated 0.5 with it, which makes DEV-2 neither easier nor harder
than DEV-1. The no-skill families do not include a skill-less strategy whose returns are positively
autocorrelated beyond 0.1, nor one whose exposure differs from its benchmark's in a way that drifts.
The 16 entries that take N from 14 to 30 are taken to be failed reruns; a completed rerun would
replace a selectable run rather than add a column, and under the null that is the same thing. A
false-pass rate says nothing about the holdout, which is contaminated (D4).

**Dependence on how development closes (decision D21).** Nothing calibrated here depends on it. The
study fixes N at 30 and at 14, whatever ledger point it would be counted to, and the candidate rules
are applied to a complete family; that development closes at the unseal rather than at a freeze
changes neither.

## Reproduce

From `research/` in a clean clone, with the pinned tools only:

```bash
nice -n 15 python -m norse_research.calibration --workers 6 --report
```

It writes `research/calibration/gate-calibration-results.json` and, between the markers of
"Results of the rerun", the results section of this report, and prints the file's SHA-256 and the
verdict. `test_the_committed_full_run_is_the_one_the_report_cites` checks that the committed file is
the one the section names, that its families, seed and registration are the code's, that its verdict
is what `calibration.verdict` computes from it, and that the section is exactly what
`calibration.results_markdown` renders from it; at the commit before the rerun it checks that
neither exists yet. The number of workers does not change the result. The counts reproduce on any
platform; the per-ledger digests reproduce on the same platform, but may differ elsewhere because
`random.gauss`, `random.gammavariate` and `NormalDist` call the C library's `log`, `exp` and `cos`,
which can differ in the last bit between systems. Plan for hours in pure Python, and keep to
`nice -n 15` and at most six workers on this Mac, which also records live market data.

**The rerun's record.** Everything ran on an Apple M4 (10 cores, 24 GB) under macOS 26.5.2 and
Python 3.13.11, with 6 worker processes at `nice -n 15`, from a clean tree at a commit pushed to
#66 with green CI before the run started. Each run read a `docs/preregistration.json` whose
SHA-256, `736d51810d666bffaa79bf4bf4e4ee6bede23ea6487feac6552df955d0cc5079`, was that of the file
on main (b656d5f) when it started. In order:

1. **A stopped attempt.** At edd9286838191b6a6f90f7ed23d9b43f6ca5fbe7 (CI run 45033694917), the
   run was started at 14:43:08 UTC on 2026-11-22 and stopped (SIGTERM) after about 17 minutes,
   when the coordinating agent asked to wait for a pending ruling on #70 that might have changed
   the registration. The study writes nothing until every family is finished, so the attempt
   wrote no file, printed only its start time and showed no figure. The ruling changed nothing
   in the registration.
2. **The first complete run.** At the same commit, from scratch, from 15:11:34 to 15:41:14 UTC
   (1,780 seconds; one-minute load average 4.02 at the start). Its results file had the SHA-256
   `26efe37f0964a95141034f5fab20eed38fcb126ebe7d79df849fffb8faeaea40`, and its results and
   verdict were seen before the renderer fix described under "The first complete run, seen
   before the rerun". It was not committed, because the report's section it wrote listed the
   four detection families in a different order from a rendering of its file.
3. **The rerun, committed here.** At c75fb8eeafa346799b8dbbc08bdec55ead96fcc4 (CI run
   45040366789), which changes only that order and adds its test, from scratch, from 15:56:49 to
   16:27:39 UTC (1,851 seconds; one-minute load average 7.93 at the start). It reproduced the
   first complete run's results file byte for byte (SHA-256 `26efe37f…`, compared with `cmp`) and
   wrote the same results section. The results file and that section are committed as the rerun
   produced them.

In both complete runs, the `prereg-v1` pooled-V counterfactual at N = 30 equals the `prereg-v1`
run's N = 30 summary exactly in all 54 families, so the simulation did not change.

CI runs a smoke version only (`test_smoke_run_reproduces_and_summarises`: two families, two ledgers
each), with the equivalence test, the model's checks, the pin of the families and the check on the
committed file.

```bash
python -m pytest tests/ -q
ruff check .
```
