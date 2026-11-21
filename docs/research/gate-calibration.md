# Gate calibration (G1.7)

Owner: quant. Written 2026-11-21. The study is
[research/norse_research/calibration.py](../../research/norse_research/calibration.py), its
tests [research/tests/test_calibration.py](../../research/tests/test_calibration.py), and
the full run's output
[research/calibration/gate-calibration-results.json](../../research/calibration/gate-calibration-results.json).
Everything here was computed on synthetic data. No price, return or performance figure was
computed on market data, nothing was read from the development or holdout files, and the
dataset store was never opened; the one repository file the study reads is
`docs/preregistration.json`, for the registered windows, grid sizes and budget.

## Verdict

To be written from the full run's results, against the readings in "Readings fixed before the
full run" below, which are committed before those results exist.

## What was calibrated

The gate as section 10 registers it and `norse_research.gate` implements it (G1.5 and G1.6, as
merged in #56 and #59): each hypothesis's candidate is its selectable run with the highest DEV-1
z, ties to the earlier configuration; it must meet DEV-1 to DEV-5; the holdout candidate is the
passing hypothesis's candidate with the higher z, ties to H1. N is the registered budget of 30
(and 14, every configuration run once, as a sensitivity). V is the sample variance of every
selectable run's daily Sharpe ratio across both hypotheses with no floor, and DEV-2's V the same
over the excess-return Sharpe ratios; DEV-2 is volatility matched for H1 and beta matched for H2
(D15). The PBO matrices have 8 and 6 columns over the windows of section 3: H1 2,802 days from
2018-05-01, H2 2,185 days from 2020-01-08, both to 2025-12-31, with calendar years for DEV-4
taken from those dates. A simulated ledger **passes** when it has a holdout candidate, that is
when at least one hypothesis passes development; that is the decision that would open the
holdout. HOLD-1 is reported separately as an end-to-end figure, on 304 further simulated days of
the holdout candidate's own process.

**How the gate is called.** Every public function of `norse_research.gate` takes only a ledger the
gate itself issued (`gate_ledger`, or `_issue` in tests), so none can be called on a simulated
return matrix, and writing 54,000 hash-chained ledgers would test the ledger rather than the
statistics. The study therefore calls the statistics those functions are built from, on the same
inputs in the same order: `gate.daily_sharpe`, `gate._deflated` (the DSR and its z, the
zero-variance rule included), `sharpe.cross_trial_sharpe_variance` for V, `gate.EXCESS_MATCH`
with `benchmark.excess_returns` for DEV-2, `cscv.cscv` with `GATE_BLOCKS` for DEV-3,
`gate.sharpe_without_largest` for DEV-5 and `gate.Development` for every threshold and
inequality. Three pieces are written out because their gate versions take the ledger: DEV-4
(`gate.sharpe_without_each_year`), the candidate rule (`gate.candidates`) and the holdout
candidate rule (`gate._holdout_candidate`), each with the gate's own expression. To prove the
composition is the gate, `test_the_study_judges_a_ledger_exactly_as_the_gate_does` writes two
simulated families into in-memory ledgers (all 14 configurations, both benchmarks and 16 failed
reruns, so N is 30), runs `gate.development` and `gate.holdout_candidate` on them, and requires
the same candidate and bit-for-bit the same DSR, z, excess DSR, PBO, DEV-4 and DEV-5 values and
the same holdout candidate, for a ledger where neither hypothesis passes and one where H1 does.
The study also checks, on every ledger, that the z it ranks by equals the z `gate._deflated`
returns for the candidate.

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

The 17 **no-skill families** are the ones the verdict rests on. Nine are what section 10 requires:
normal, serially independent returns, benchmarks unrelated to the strategies and without drift,
correlated within each hypothesis at 0, 0.5, 0.9, 0.99 and 0.999 and across the two from 0 up to the
within value, including the near-identical families in which the two forms of V differ most.
Three give the configurations market exposure without skill (H1 correlated 0.7 with B-H1, as a long
and flat rule is; H2 0.2 or 0.5 with B-H2) in a market with an annualised Sharpe ratio of 0.5 or 1.0,
the case DEV-2 exists for and the one that calibrates D15's beta-matched excess. Two are the power
families' layout with nothing planted. Three add fat tails and a volatility regime at correlations 0,
0.9 and 0.99. Two **sensitivity families**, serially correlated returns, are reported but not judged.

The 35 **power families** plant an edge in one hypothesis, the other without skill. H1's
configurations are correlated 0.5 with B-H1, so that their volatility-matched excess has exactly
the planted Sharpe ratio (section 12's formula (S - S_B) / sqrt(2 - 2 rho) with S_B = 0 and rho =
0.5); H2's are uncorrelated with B-H2, so that the beta-matched excess has it too. For each
hypothesis, a **single planted column** (the first registered configuration) or a **shared edge**
(every configuration of the hypothesis), at Sharpe ratios of 1.0, 1.5 and 2.0, with correlations
of 0.5 within and 0 across ("low") or 0.9 within and 0.5 across ("high"); a single column also at
2.5 and 3.0 to show where power reaches one half and four fifths; and, for contrast, both
hypotheses sharing one edge of the same size.

**Seeds.** Ledger i of family f draws from Python's `random.Random("20261121/f/i")`, so any ledger
can be rebuilt alone (`calibration.simulate`). The study seed 20261121 was fixed before the first
run.

**What the study computes.** On every ledger: the outcome at N = 30 (registered), at N = 14, and
under a counterfactual that is not registered, with V over each hypothesis's own runs only (at
N = 30), which isolates the effect of pooling. The PBO is computed for a hypothesis whenever it
can decide the outcome (its candidate meets every other criterion under any of the three
judgements) and always for the first 200 ledgers of each family, from which DEV-3's own pass
rate is reported; where it is not computed the hypothesis fails another criterion anyway, so
whether a ledger passes is exact for every ledger.

## Readings fixed before the full run

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
   1.86 (H2) at N = 30, above 1.5. One half is not generous either: section 12's even-odds DEV-1
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
code (8e89a02) and in this section (the commit that adds it), while the full run was still
computing and before its results file existed. After the pilot the only change was to add the
single-column families at Sharpe ratios of 2.5 and 3.0, to locate where power reaches one half;
no judged family, reading, seed, threshold or line of the gate was changed. The full run's
results are reported against this definition as it stands, whatever they show.

## Results

To be added from the full run.
