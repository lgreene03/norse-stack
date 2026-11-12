"""Known-answer and synthetic checks for CSCV PBO. Synthetic data only."""

import math
import random
from itertools import pairwise

import pytest
from norse_research.cscv import GATE_BLOCKS, block_bounds, cscv


def test_gate_uses_16_blocks_and_12870_splits():
    # Bailey, Borwein, Lopez de Prado and Zhu, Eq. (2.3): C(S, S/2) splits. The
    # paper's text says "12,780" for S = 16, a transposition of C(16, 8) = 12,870.
    rng = random.Random(1)
    trials = [[rng.gauss(0, 1) for _ in range(64)] for _ in range(3)]
    result = cscv(trials)
    assert GATE_BLOCKS == 16
    assert result.splits == math.comb(16, 8) == 12_870
    assert len(result.logits) == result.splits
    assert cscv(trials, blocks=4).splits == 6  # the paper's Figure 1, S = 4


def test_blocks_differ_by_at_most_one_row():
    bounds = block_bounds(1000, 16)
    sizes = [end - start for start, end in bounds]
    assert sizes == [63] * 8 + [62] * 8
    assert bounds[0][0] == 0
    assert bounds[-1][1] == 1000
    assert all(a[1] == b[0] for a, b in pairwise(bounds))


# --- Hand-computed cases (S = 2, two splits) ---


def test_selection_that_reverses_out_of_sample_is_full_overfit():
    # Trial A is best on the first half and worst on the second; B the reverse.
    # Each split's in-sample winner ranks 1 of 2 out of sample: w = 1/3,
    # logit ln(1/2) < 0, so PBO = 1.
    a = [0.03, 0.01, -0.01, -0.03]
    b = [-0.03, -0.01, 0.01, 0.03]
    result = cscv([a, b], blocks=2)
    assert result.splits == 2
    assert result.logits == pytest.approx((math.log(0.5), math.log(0.5)))
    assert result.pbo == 1.0
    assert result.prob_oos_loss == 1.0


def test_selection_that_persists_is_no_overfit():
    # A beats B on both halves: the in-sample winner ranks 2 of 2 out of sample,
    # w = 2/3, logit ln 2 > 0, PBO = 0.
    a = [0.03, 0.01, 0.02, 0.04]
    b = [0.01, -0.01, -0.02, 0.00]
    result = cscv([a, b], blocks=2)
    assert result.logits == pytest.approx((math.log(2), math.log(2)))
    assert result.pbo == 0.0
    assert result.prob_oos_loss == 0.0


def test_identical_trials_count_as_overfit():
    # Every trial scores the same on every half: the mean rank is the median, the
    # logit is exactly 0, which counts as overfit (no better than the median).
    rng = random.Random(3)
    base = [rng.gauss(0, 1) for _ in range(64)]
    result = cscv([base, list(base), list(base)], blocks=4)
    assert set(result.logits) == {0.0}
    assert result.pbo == 1.0


def test_flat_trial_scores_zero():
    # A trial with no position at all (all zero returns) has no variance on any
    # half and is scored 0, between losers and winners, rather than failing.
    winner = [0.01 + 0.001 * (i % 3) for i in range(32)]
    loser = [-x for x in winner]
    flat = [0.0] * 32
    result = cscv([winner, flat, loser], blocks=4)
    # The winner wins every split and ranks 3 of 3: w = 3/4, logit ln 3.
    assert result.logits == pytest.approx((math.log(3),) * 6)
    assert result.pbo == 0.0


def test_rejects_malformed_input():
    with pytest.raises(ValueError):
        cscv([[0.1, 0.2] * 16])  # one trial
    with pytest.raises(ValueError):
        cscv([[0.1] * 32, [0.1] * 31], blocks=4)  # ragged
    with pytest.raises(ValueError):
        cscv([[0.1] * 32, [0.2] * 32], blocks=5)  # odd S
    with pytest.raises(ValueError):
        cscv([[0.1] * 31, [0.2] * 31])  # under two rows per block


# --- Known answer: the paper's accuracy study ---
#
# Bailey, Borwein, Lopez de Prado and Zhu, revised version of November 2014,
# Section 7 ("Accuracy of the test", the test cases of Section 6), Figure 6
# ("CSCV's accuracy"); in the published version these are the appendices at
# SSRN 2568435. Each matrix has N - 1 trials rescaled to an annualised Sharpe
# ratio of exactly 0 and one to exactly SR_Case, over T daily returns at
# 365.25 * 5 / 7 a year (their Snippet 1). For each (SR_Case, T, N) the paper prints
# the probability by Monte Carlo (Prob_MC, 1,000 experiments, one first-half and
# second-half split each), by extreme value theory (Prob_EVT), and the mean and
# standard deviation of its own CSCV estimates over 1,000 matrices.
#
# Rows rescaled to fixed column moments are exchangeable, so every balanced split
# has the hold-out's overfitting probability and E[CSCV PBO] equals Prob_MC and
# Prob_EVT. The test asks that the mean over 40 matrices lies within 0.035 of
# Prob_EVT and within one published Std_CSCV of the published Mean_CSCV. (The
# paper's own CSCV means run 0.00 to 0.05 above its EVT values; ours sit closer
# to EVT. See docs/research/gate-statistics.md.)

PAPER_FREQ = 365.25 * 5 / 7

ACCURACY_ROWS = [
    # SR_Case, T, N, Mean_CSCV, Std_CSCV, Prob_MC, Prob_EVT
    (0, 1000, 10, 1.000, 0.000, 1.000, 1.000),
    (1, 500, 10, 0.618, 0.054, 0.650, 0.608),
    (1, 1000, 10, 0.399, 0.054, 0.354, 0.360),
    (1, 2500, 10, 0.123, 0.048, 0.093, 0.086),
    (2, 500, 10, 0.186, 0.054, 0.146, 0.137),
    (2, 1000, 10, 0.041, 0.027, 0.011, 0.023),
]


def paper_column(rng, length, sr_annual):
    xs = [rng.gauss(0, 1) for _ in range(length)]
    mean = sum(xs) / length
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / length)  # numpy's std, ddof 0
    mu, sigma = sr_annual / PAPER_FREQ, 1 / math.sqrt(PAPER_FREQ)
    return [(x - mean) / sd * sigma + mu for x in xs]


@pytest.mark.parametrize(
    ("sr_case", "t", "n", "mean_cscv", "std_cscv", "prob_mc", "prob_evt"), ACCURACY_ROWS
)
def test_paper_accuracy_table(sr_case, t, n, mean_cscv, std_cscv, prob_mc, prob_evt):
    rng = random.Random(1000 * sr_case + t + n)
    reps = 40
    values = []
    for _ in range(reps):
        trials = [paper_column(rng, t, 0.0) for _ in range(n - 1)]
        trials.append(paper_column(rng, t, sr_case))
        values.append(cscv(trials).pbo)
    mean = sum(values) / reps
    assert abs(mean - prob_evt) <= 0.035
    assert abs(mean - mean_cscv) <= max(std_cscv, 0.001)
    assert abs(mean - prob_mc) <= 0.05


# --- Synthetic checks ---


def test_pure_noise_trials_average_half():
    # IID noise, not rescaled, even N: the in-sample winner's out-of-sample rank is
    # uniform, so E[PBO] = 1/2 exactly. A single matrix varies a lot; the mean of
    # many does not.
    rng = random.Random(20261111)
    reps, n, t = 60, 10, 320
    values = [cscv([[rng.gauss(0, 1) for _ in range(t)] for _ in range(n)]).pbo for _ in range(reps)]
    assert abs(sum(values) / reps - 0.5) <= 0.1


def test_one_planted_strong_trial_gives_low_pbo():
    # Nine noise trials and one with an annualised Sharpe of 3 over four years.
    rng = random.Random(42)
    t = 1460
    daily = 3 / math.sqrt(365)
    trials = [[rng.gauss(0, 1) for _ in range(t)] for _ in range(9)]
    trials.append([rng.gauss(daily, 1) for _ in range(t)])
    result = cscv(trials)
    assert result.pbo < 0.1
    assert result.prob_oos_loss < 0.1
