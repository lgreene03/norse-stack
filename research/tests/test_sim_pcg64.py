"""norse_research.sim.pcg64 reproduces numpy's default_rng bit for bit (G1.9; section 8).

The known answers in fixtures/numpy-rng-vectors.json were generated once by real numpy
(its version is recorded there) with fixtures/numpy-rng-vectors.py, in a virtual
environment outside the repository. They cover SeedSequence's state words, PCG64's raw
outputs, all 200,000 doubles of ``random`` (C-H1's longest call is 158,821) by SHA-256
with sampled values, C-H2's two ``integers`` draws, other 32-bit ranges including heavy
rejection, and interleaved calls that share PCG64's buffered half word. The last test
compares with numpy directly when numpy is importable, and is skipped otherwise.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from norse_research.sim import SimulationError, default_rng
from norse_research.sim.pcg64 import PCG64, SeedSequence

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "numpy-rng-vectors.json").read_text())
SEEDS = VECTORS["seeds"]
IDS = [f"seed {s['seed']}" for s in SEEDS]


def test_the_vectors_are_numpys_and_cover_the_controls():
    assert VECTORS["numpy_version"] == "2.5.5"
    assert [s["seed"] for s in SEEDS] == [0, 1, 42, 99]
    assert all(s["random"]["draws"] >= VECTORS["c_h1_n"] for s in SEEDS)


@pytest.mark.parametrize("vector", SEEDS, ids=IDS)
def test_seed_sequence_and_pcg64_raw_outputs_match(vector):
    seed = vector["seed"]
    assert SeedSequence(seed).generate_state(4, 64) == vector["seed_words"]
    generator = PCG64(seed)
    assert [generator.next64() for _ in range(8)] == vector["next64"]


@pytest.mark.parametrize("vector", SEEDS, ids=IDS)
def test_random_matches_every_double_of_the_longest_control_call(vector):
    expected = vector["random"]
    doubles = default_rng(vector["seed"]).random(expected["draws"])
    packed = struct.pack(f"<{len(doubles)}d", *doubles)
    assert hashlib.sha256(packed).hexdigest() == expected["sha256_float64_le"]
    for index, value in expected["sample"].items():
        assert doubles[int(index)].hex() == value
    # C-H1 flips a day's sign where rng.random(n) < 0.5.
    flips = sum(d < 0.5 for d in doubles[: VECTORS["c_h1_n"]])
    assert flips == expected["below_half_in_first_c_h1_n"]


@pytest.mark.parametrize("vector", SEEDS, ids=IDS)
def test_integers_match_c_h2_and_other_32_bit_ranges(vector):
    m = VECTORS["development_settlements_m"]
    rng = default_rng(vector["seed"])
    # C-H2: k = rng.integers(540, M - 540, endpoint=True), BTC drawn before ETH.
    assert [rng.integers(540, m - 540, endpoint=True) for _ in range(2)] == vector["c_h2_k"]
    for case in vector["integers"]:
        rng = default_rng(vector["seed"])
        draws = [rng.integers(case["low"], case["high"], endpoint=case["endpoint"])
                 for _ in range(len(case["draws"]))]
        assert draws == case["draws"], case


@pytest.mark.parametrize("vector", SEEDS, ids=IDS)
def test_interleaved_calls_share_the_buffered_half_word(vector):
    rng = default_rng(vector["seed"])
    for call, expected in vector["mixed"]:
        if call == "integers":
            assert rng.integers(0, 1000) == expected
        else:
            assert rng.random().hex() == expected


def test_what_is_not_ported_is_refused():
    rng = default_rng(0)
    for call in (lambda: rng.integers(0, 2**40), lambda: rng.integers(0, 10, size=3),
                 lambda: rng.integers(5, 4), lambda: default_rng(-1), lambda: rng.random(-1)):
        with pytest.raises(SimulationError):
            call()


def test_matches_numpy_directly_when_numpy_is_installed():
    """Not run in CI, which installs no numpy: every registered control seed, 0 to 99,
    against the installed numpy."""
    np = pytest.importorskip("numpy")
    for seed in range(100):
        ours, theirs = default_rng(seed), np.random.default_rng(seed)
        assert ours.random(2000) == theirs.random(2000).tolist()
        assert [ours.integers(540, 6036, endpoint=True) for _ in range(2)] == [
            int(theirs.integers(540, 6036, endpoint=True)) for _ in range(2)]
