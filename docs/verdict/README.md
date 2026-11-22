# Verdict texts written before the result (G3.1)

The CTPO role, an AI agent the owner runs, wrote these texts on 2026-11-21 for goal G3.1 of
[ITERATION_GOALS.md](../ITERATION_GOALS.md): both outcomes drafted before the unseal, so that
the result cannot shape the story told about it. They are the section of the repository's
README that will report the pre-registered verdict on H1 and H2, written once for every
outcome the [pre-registration](../PREREGISTRATION.md) and the iteration's kill criteria allow,
while no result exists. When the verdict is known, README.md gains exactly one of them, the one
the rules below select, with its placeholders filled and nothing else changed. No wording about
the result is written after the result.

## When they were written

All seven texts were written on 2026-11-21, before any trial had run, and were committed
together in this commit. At that moment the trial ledger did not yet exist: the remote had no
`ledger` branch, so there was no genesis entry and no trial, benchmark or control run, and the
only pre-registration tag was `prereg-v1`, on 80a5e0c. The gate's calibration (G1.7) had not
started. The author had seen no price, return or performance figure of H1, H2, their
benchmarks or their controls, and none had been computed through the harness.

The commit is the proof, not this paragraph. Once the pull request has merged,
`git log --diff-filter=A --format='%H %cI' -- docs/verdict/` prints the commit on main that
added these files and its date, and GitHub's record of the merge gives a time that no one
working on a local clone can set, which the [ledger threat model](../research/ledger-threat-model.md)
treats as the only trustworthy kind (T11). Two further checks tie the texts to the ledger. Each
runs `git merge-base --is-ancestor A B`, where A is the last commit on main that changed
`docs/verdict/` (`git log -1 --format=%H -- docs/verdict/`). With B the commit recorded by the
first ledger entry after the genesis entry, it shows the texts were on main before any
benchmark, control or trial ran. With B the commit the unseal entry records, where there is
one, it shows they were on main before the holdout was opened, which is what G3.1 requires and
what the threat model means by an unseal that is not early (T20). Both checks hold only if this
directory merges to main before the first ledger entry after the genesis entry.

## Which text is published

No text is published while the ledger holds an unseal entry whose holdout run has neither
returned a result nor used up the runs the seal allows (G3.2), or while a holdout candidate
exists, both grids are complete or exhausted, and the ledger holds no unseal entry (section 11,
"When the verdict waits"); a development pass beside a grid that is neither complete nor
exhausted does not wait (decision D23). None that cites the security
attestation (G3.4) or the independent reproduction (G3.5) is published before both exist. Then
the rules below are applied in order, and the first that matches selects the text. They read
only the ledger's verification, the calibration report, the verdict words the gate generates
and whether the ledger holds an unseal entry, so no one chooses.

| Rule | When | Text |
|---|---|---|
| 0 | The ledger's hash chain is broken, so the iteration is void | [OUTCOME_VOID.md](OUTCOME_VOID.md) |
| 1 | The gate did not pass its calibration (G1.7), including a calibration that never completed, so no strategy was judged | [OUTCOME_GATE_NOT_CALIBRATED.md](OUTCOME_GATE_NOT_CALIBRATED.md) |
| 2 | Either hypothesis is PASS | [OUTCOME_PASS.md](OUTCOME_PASS.md) |
| 3 | Either hypothesis is NOT RUN, and neither is PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT | [OUTCOME_NOT_RUN.md](OUTCOME_NOT_RUN.md) |
| 4 | Either hypothesis is PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT | [OUTCOME_PASSED_DEVELOPMENT_NOT_TESTED.md](OUTCOME_PASSED_DEVELOPMENT_NOT_TESTED.md) |
| 5 | Both are FAIL and the ledger holds an unseal entry | [OUTCOME_FAIL_ON_HOLDOUT.md](OUTCOME_FAIL_ON_HOLDOUT.md) |
| 6 | Both are FAIL and there is no unseal entry | [OUTCOME_FAIL.md](OUTCOME_FAIL.md) |

Every combination that section 11 of the pre-registration allows lands in exactly one row. A
pass by either hypothesis, whatever the other's verdict (FAIL, PASSED DEVELOPMENT, NOT TESTED ON
THE HOLDOUT, or NOT RUN), is rule 2. A hypothesis that is NOT RUN with no pass beside it is rule
3, whether the other failed in development, failed on the holdout or is NOT RUN as well, but not
when the other passed development. PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT without a pass
beside it means either that the other hypothesis took the holdout and failed it, or, under
decision D23, that the other's grid was neither complete nor out of budget when the verdict was
given, so no unseal could happen and the other is NOT RUN; both are rule 4, which is why rule 3
leaves out a NOT RUN beside a development pass. Section 11 puts that verdict word before FAIL,
so a development pass is never published as a fail because the holdout stayed sealed. Two fails are rule 5 when one
of them passed development and failed on the holdout, including a holdout run that never
returned a result, and rule 6 when neither passed development, so the holdout was never opened.
A fail because a hypothesis's entries and the reserve ran out is a FAIL like any other. Rules 0
and 1 come first because they come from the iteration's kill criteria, not from section 11's
table, which has no row for either (open questions 1 and 11).

## How the published section is made

The selected text goes into README.md as a top-level section headed "Pre-registered verdict on
H1 and H2", as the first section after the title and badges, whatever the outcome, so that its
place on the page cannot be used to raise a pass or bury a fail. Other README changes that
follow from the outcome (G3.6 on a fail reframes the trading panels, G0.11 the older claims)
come in their own pull requests and never edit this section.

Every `{{NAME}}` is replaced by the value the generated verdict gives it (the verdict memo of
G3.3 and the generated page of G3.6), copied rather than typed, since the iteration's interface
rules say verdicts are generated from data. A figure that does not exist for the outcome is
filled with the words "not computed", never 0 and never left blank; every cell of a holdout
table for a holdout that was never opened reads "not opened"; and for a holdout run that never
returned a result, the result entry reads "none", the holdout figures "not computed" and HOLD-1
"not met". Verdict words are written exactly as section 11 writes them, and each criterion's
result reads "met", "not met" or "not computed". Every criterion and both candidate rules are
applied to unrounded values. Sharpe ratios are annualised by the square root of 365 and shown
to two decimal places, as is the HOLD-1 bound; deflated Sharpe ratios and the probability of
backtest overfitting are shown to four; returns are percentages to one decimal place, a year
being 365 days; and any figure that would round onto its pass mark is shown with as many more
digits as it takes to tell them apart. Commit and entry hashes are given in full. Links in these
files are written relative to the repository root, where the section is published, so they do
not resolve when read here.

Nothing else changes: no sentence is added, removed or reworded, and no emphasis moves. Anyone
can check a published section by taking the selected file at the drafting commit, turning each
`{{NAME}}` into a wildcard, and matching the published section against it; the only
differences allowed are the filled values.

## Placeholders

`H1_*` and `H2_*` are the same figure for each hypothesis. "The holdout hypothesis" is the one
whose candidate took the holdout, and "the other hypothesis" is the remaining one. Where no
candidate took the holdout because the other grid was unfinished (decision D23), "the other
hypothesis" of OUTCOME_PASSED_DEVELOPMENT_NOT_TESTED.md is the one that passed development, and
every holdout placeholder reads "not opened".

| Placeholder | Value |
|---|---|
| `H1_VERDICT`, `H2_VERDICT` | the hypothesis's verdict word from section 11 |
| `H1_SELECTABLE` | the number of registered configurations with a selectable run (section 9, rule 4) |
| `H1_TRIALS`, `H1_RESERVE` | trial entries the hypothesis used from its own 12, and from the shared reserve of 6 |
| `H1_CANDIDATE` | the candidate's configuration identifier and label, such as "H1-03, slow lookbacks" |
| `H1_RETURN` | the candidate's compound annual net return over its development window, a year being 365 days |
| `H1_SHARPE`, `H1_SHARPE_INTERVAL` | the candidate's annualised net Sharpe ratio, and its registered 90% block bootstrap interval written "a to b" |
| `H1_BENCHMARK_SHARPE` | the annualised Sharpe ratio of B-H1 or B-H2 over the same window |
| `H1_DSR`, `H1_DEV1` | DEV-1's deflated Sharpe ratio and its result |
| `H1_EXCESS_DSR`, `H1_DEV2` | DEV-2's deflated Sharpe ratio of the excess return and its result |
| `H1_PBO`, `H1_DEV3` | DEV-3's probability of backtest overfitting and its result |
| `H1_YEAR_OUT_SHARPE`, `H1_YEAR_OUT_YEAR`, `H1_DEV4` | DEV-4's lowest Sharpe ratio with one calendar year removed, the year whose removal gives it, and the result |
| `H1_BEST20_OUT_SHARPE`, `H1_DEV5` | DEV-5's Sharpe ratio without the 20 largest daily returns, and its result |
| `H1_DEVELOPMENT` | "yes" if the candidate met DEV-1 to DEV-5, "no" if not, "not computed" for a hypothesis that is NOT RUN |
| `H1_CONTROL_PERCENTILE` | the candidate's percentile among its hypothesis's 100 control runs' Sharpe ratios (section 8) |
| `HOLDOUT_HYPOTHESIS`, `HOLDOUT_CANDIDATE` | the holdout hypothesis ("H1" or "H2") and its candidate, written as for `H1_CANDIDATE` |
| `OTHER_HYPOTHESIS`, `OTHER_VERDICT`, `OTHER_CANDIDATE` | the other hypothesis, its verdict word and its candidate |
| `UNSEAL_TIME`, `UNSEAL_ENTRY`, `UNSEAL_COMMIT` | the unseal's time as GitHub recorded the push (UTC), its ledger entry hash, and the commit on main it records |
| `HOLDOUT_RUNS` | how many holdout runs reached the holdout data |
| `HOLDOUT_RESULT_ENTRY` | the ledger entry holding the holdout result, or "none" |
| `HOLDOUT_RETURN`, `HOLDOUT_SHARPE`, `HOLDOUT_BOUND`, `HOLD1` | the compound net return over the 304 holdout days, the annualised holdout Sharpe ratio, HOLD-1's lower bound annualised, and HOLD-1's result |
| `HOLDOUT_CANDIDATE_MINTRL` | the candidate's minimum track record length against a Sharpe ratio of 0, in days, from its development statistics (section 10) |
| `N` | the number of trial entries counted in N (section 9) |
| `H1_SR0`, `H2_SR0` | the hypothesis's DEV-1 SR0 (section 10) annualised: the Sharpe ratio the best of N independent skill-less trials over its window is expected to show, with the ledger's N and V = 1/(T − 1) of the window (decision D22), so it differs between the hypotheses only through their windows' lengths |
| `DEV_CLOSE_ENTRY`, `DEV_CLOSE_DATE` | the hash and date of the last ledger entry counted in N: under `prereg-v1`, the last entry in the frozen ledger; under `prereg-v2` (decision D21), the last entry before the unseal entry, or with no unseal entry the last entry at `LEDGER_HEAD` (open question 4) |
| `LEDGER_HEAD`, `LEDGER_TAG` | the ledger entry hash the verdict was computed at, and the `ledger-*` milestone tag marking it; G1.5 tags the head at each milestone, and no verdict is computed at an untagged head |
| `PREREG_TAG`, `PREREG_COMMIT` | the pre-registration tag the genesis entry names, and its commit |
| `DEV_MANIFEST_SHA256`, `HOLDOUT_MANIFEST_SHA256` | the manifests' SHA-256, which must equal the registered c27499e97edd1d2288a7d38a7f0a890f9be108919dda22f7e3c9c119e725a55d and 806e1d6acdd90c5454297b78c42c37cfb6f522ebcb81f01b773ba66b9308ea77 unless a later `prereg-*` tag changed them |
| `CAL_NOSKILL_PASS_RATE`, `CAL_FALSE_PASS_RESULT` | the share of the 1,000 no-skill ledgers the full gate passed in its worst no-skill family, as a percentage, and whether the 5% limit was met in every family (section 10, "Calibration") |
| `CAL_DETECTION_H1`, `CAL_DETECTION_H2` | with one configuration carrying a planted net Sharpe ratio of 2.0, the share of ledgers in which the planted hypothesis passed development, in the lower of its two such families, as a percentage |
| `CAL_DETECTION_RESULT` | whether the detection half was met: more than half in each of the four single-planted families at 2.0, at N = 30 (section 10, "Calibration"; decision D22) |
| `CAL_POWER_H1_SINGLE`, `CAL_POWER_H2_SINGLE` | reported only: the share of ledgers in which the planted hypothesis passed development when one configuration carries a planted net Sharpe ratio of 1.5, as a percentage |
| `CAL_POWER_H1_SHARED`, `CAL_POWER_H2_SHARED` | the same when every configuration of the hypothesis shares that edge |
| `CAL_REPORT`, `CAL_COMMAND` | the path of the calibration report (G1.7), and the command that reruns it |
| `GENERATED_AT` | when the figures were generated (UTC) |
| `VERDICT_COMMIT` | the commit on main at which the verdict, or the verification that voided it, was computed |
| `VERDICT_COMMAND` | the command that recomputes every figure of the section from the ledger, as the independent reproduction (G3.5) ran it |
| `ATTESTATION` | the path of the security attestation's committed output (G3.4) |
| `REPRODUCTION`, `REPRODUCTION_DATE` | the path of the independent reproduction's record (G3.5) and its date |
| `VERDICT_MEMO` | the path of the verdict memo (G3.3) |
| `VOID_DATE`, `VOID_ENTRY`, `VOID_REPORT` | the date verification first found the chain broken, the first entry at which it breaks, and the path of the committed verification record |
| `DRAFT_DATE`, `DRAFT_COMMIT` | the date and hash of the last commit on main that changed the published file |

## Change control

These texts may be corrected after review until the first ledger entry that follows the genesis
entry, the same point at which section 16 of the pre-registration closes, and each correction is
its own commit with its reason in the change log below. After that point they do not change,
and after the unseal no one may change them for any reason. A defect found later is published
as a dated erratum below the section, never as an edit within it.

| Date | Commit | Change |
|---|---|---|
| 2026-11-21 | 24b7b7c | all seven texts and this page, first version, revised before commit after the quant role's review |
| 2026-11-22 | 85dc01f | for `prereg-v2` (#65): decision D22's null variance, so `SR0` becomes `H1_SR0` and `H2_SR0` and the five texts that state it no longer say it depends on how the runs differed; the calibration's restated clause (a planted 2.0 in each of four families, the planted hypothesis passing development), so rule 1's text, its table and the calibration placeholders change, the 1.5 figures are reported only, and open question 2 is settled; and decision D23, so PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT also covers a development pass beside an unfinished grid, rule 3 leaves that combination to rule 4, and rule 4's text covers both of its cases. Corrections made after the quant and security roles' reviews of #65, which are AI agent roles, not approvals |
| 2026-11-22 | this commit | after the quant role's second review of #65 (an AI agent role, not an approval): the publication rule no longer blocks decision D23's outcome, waiting only while an unseal's holdout run is pending or a holdout candidate exists with both grids closed and no unseal; a PASSED DEVELOPMENT hypothesis "was not tested on the holdout in this iteration" in place of "can never be tested"; D24 reads "not revised again in this iteration" in rule 1's text; open questions 1, 4, 9, 10 and 11 are marked settled by `prereg-v2`; and "if adopted" is dropped from D21 |

## Review

G3.1 is done only when the texts are written and reviewed. The quant role checks every
statement against the registration; the interface role checks the texts against the
iteration's rules for presenting results; the owner, the only person on the project, decides
whether the fail texts would be published as readily as the pass text. The roles are AI agents,
so their reviews are not independent approvals. The quant role reviewed a first draft on
2026-11-21 and asked for changes, all made before this commit: the 2026 window is claimed only
for the holdout candidate, a holdout run that never returned is no longer called the failure
the holdout exists to catch, NOT RUN uses section 9's selectable run, calibration power is
reported for a single planted configuration and for a shared edge per hypothesis, the minimum
track record length is stated as a lower bound, rounding cannot move a figure across its mark,
and the void outcome and open questions 9 to 11 were added. Its review of this version, and the
interface role's and the owner's, are recorded on the pull request.

## Open questions the registration leaves

Writing the texts found places where the registration does not settle the public story. Each
is recorded with its severity and a recommendation for the owner and the quant role; once
decided, it belongs in [DECISIONS.md](../DECISIONS.md). The texts are written to stay true
whichever way each is decided, except where a question says otherwise.

1. **Medium. Calibration failure has no row in the verdict table. Settled by `prereg-v2`:**
   section 11's NOT JUDGED row, read before every row but VOID, covers a calibration that was
   not passed or never completed (confirmed by the owner in decision D22; decision D24 for a
   failed rerun), and rule 1's text is published. The question as first written follows.
   Section 10 and the kill
   criteria say no strategy is judged, but section 11's table would call a hypothesis NOT RUN if
   no trial ran and could call it FAIL if trials had already run. Recommendation: the kill
   criterion governs, a calibration that never completed counts as not passed, and the
   published text is rule 1's, which carries no verdict word for either hypothesis.
2. **Medium. "Detects a planted Sharpe of 1.5" has no registered number. Settled on 2026-11-22
   by decision D22:** section 10 now registers the number, at N = 30, in each of the four
   single-planted families at an annualised Sharpe ratio of 2.0, the planted hypothesis passing
   development in more than 500 of 1,000 ledgers, with power at 1.5 reported; and, by decision
   D24, a calibration rerun that misses it closes the iteration NOT JUDGED, which rule 1's text
   publishes. The question as first written follows. Section 10 and G1.7
   ask the gate to detect it but name no power, the hypothesis, N, the family form or DEV-2's
   synthetic benchmark. A whole-gate bar of even odds would probably not be met: at N = 30
   DEV-1 alone needs 1.52 for H2 (section 12), and a shared edge roughly halves power again
   (section 13). Recommendation, the quant role's: treat the bar as a check that the gate is not
   degenerate, for example that the full gate passes a single planted 1.5 at least one time in
   four on each hypothesis at N = 30; fix it in writing, dated, before G1.7 runs; and publish
   all the power figures. Rule 1's text cites the bar and the date it was fixed.
3. **Medium. A holdout run that never returns is a FAIL.** Section 11 calls anything short of
   HOLD-1 holding a fail, and the seal caps how many runs may reach the holdout, so a data
   loading defect found after the unseal leaves the candidate failed without a holdout return
   ever seen; holdout-seal.md already asks the quant and the owner to accept that price
   explicitly. Recommendation: accept it as registered, since it errs against the strategy as
   decision D12 does. The texts say, where it can arise, that such a fail says nothing about
   2026.
4. **Medium. Nothing closes development when there is no unseal. Settled by `prereg-v2`:**
   section 11, "What closes development when there is no holdout candidate", fixes the point
   (decision D21, confirmed by the owner in D22). The question as first written follows.
   Under `prereg-v2` the unseal entry closes development, but with no holdout candidate
   there is no unseal, and the gate then reads the whole ledger. Choosing the close after
   seeing provisional figures would let them pick it. Recommendation: fix in writing, before
   the genesis entry, the date or condition at which development closes when there is no
   unseal, and tag the ledger head there with a milestone `ledger-*` tag; the texts cite that
   entry, its date and its tag.
5. **Low. What becomes of a hypothesis that is NOT RUN.** Under `prereg-v1` nothing runs after
   the freeze; under `prereg-v2` the registration is silent. Recommendation: it is never
   completed under this registration, and any later run of it is a new registration and a new
   verdict, which is how the NOT RUN text is written.
6. **Low. What follows a development pass that the holdout never tested.** The kill criteria
   stop strategy search only when both hypotheses fail, while NEXT_ITERATION.md retires the
   trading claim when none passes. Recommendation: treat it as none passing, and make any
   forward test of the untested candidate a new registration, which is how rule 4's text is
   written.
7. **Low. Which tag governs.** Whether `prereg-v1` or a `prereg-v2` under decision D21 governs
   changes what "when development closed" means, the freeze or the unseal entry. The texts carry
   the tag and the closing entry as placeholders, so they hold either way.
8. **Low. Timing of this merge.** The proof that the texts preceded every trial needs this
   directory on main before the first ledger entry after the genesis entry. Recommendation:
   merge it before the genesis entry is written.
9. **Medium. An exhausted budget beside a development pass. Settled by `prereg-v2`:** section
   11 reads PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT before FAIL (decision D23), so a
   development pass never falls to the exhaustion clause. The question as first written
   follows. Section 11's FAIL row also applies
   "whenever the hypothesis's own entries and the reserve are exhausted without a pass", which,
   read literally, fails a hypothesis that completed its grid and passed development but did not
   take the holdout. Recommendation: exhaustion only turns NOT RUN into FAIL, which is what
   section 15, item 10 was written to settle. The texts' definition ("ran out before it could
   pass") holds under either reading.
10. **Medium. Unsealing before both grids are complete. Settled by `prereg-v2`:** the unseal
   waits until each hypothesis's grid is complete or its budget exhausted, and the seal, the
   append and replay refuse an earlier one (section 11, item 4; decision D21, confirmed by the
   owner in D22). The question as first written follows. Under
   `prereg-v2` the owner could unseal once one hypothesis has passed development while the other
   is still running, which keeps N low and leaves the other NOT RUN: the early stopping that run
   rule 1 forbids within a hypothesis, done across the two. Recommendation, the quant role's:
   allow the unseal only when both grids are complete or both budgets are exhausted.
11. **Low. A broken chain. Settled by `prereg-v2`:** section 11's VOID row gives the verdict for
   both hypotheses, read before every other row, and the iteration restarts from
   pre-registration (confirmed by the owner in decision D22); rule 0's text is published. The
   question as first written follows. The kill criteria void the iteration and restart it from
   pre-registration if the ledger's chain breaks, but section 11 has no verdict for it and
   nothing says whether a restart may keep the same holdout. Recommendation: rule 0's text is
   published; a restart keeps the holdout only if it was never opened, and otherwise registers a
   new one.
