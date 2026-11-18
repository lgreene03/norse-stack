"""numpy's ``default_rng``, bit for bit, in the standard library (G1.9; section 8).

Section 8 of docs/PREREGISTRATION.md registers the control runs' random numbers as
numpy's: ``rng = numpy.random.default_rng(seed)``, then C-H1's single
``rng.random(n) < 0.5`` and C-H2's ``rng.integers(540, M - 540, endpoint=True)``, BTC
drawn before ETH on the same generator. numpy is neither in the standard library nor in
the strategy allowlist, so this module reproduces exactly what those calls return:

* ``SeedSequence`` seeding from a non-negative integer (numpy/random/bit_generator.pyx);
* the ``PCG64`` bit generator, PCG's 128-bit linear congruential step with XSL-RR output,
  seeded from four 64-bit words of the seed sequence (numpy/random/src/pcg64/pcg64.h and
  pcg64.c), including its buffered upper half for 32-bit draws;
* ``Generator.random``: each double is the top 53 bits of one 64-bit output over 2^53
  (numpy/random/src/distributions/distributions.c);
* ``Generator.integers`` for one int64 value from a range that fits 32 bits, by Lemire's
  method without masking, as numpy's default does (numpy/random/_bounded_integers.pyx.in
  and distributions.c). Wider ranges, arrays and other dtypes are refused rather than
  ported untested: no registered control uses them.

research/tests/test_sim_pcg64.py holds it to vectors numpy 2.5.5 generated
(fixtures/numpy-rng-vectors.json, with the generating script beside it).
"""

from __future__ import annotations

import itertools

from .model import SimulationError

MASK32 = 0xFFFFFFFF
MASK64 = (1 << 64) - 1
MASK128 = (1 << 128) - 1

# numpy/random/bit_generator.pyx: SeedSequence's hash constants.
DEFAULT_POOL_SIZE = 4
INIT_A = 0x43B0D7E5
MULT_A = 0x931E8875
INIT_B = 0x8B51F9DD
MULT_B = 0x58F38DED
MIX_MULT_L = 0xCA01F9DD
MIX_MULT_R = 0x4973F715
XSHIFT = 16

# numpy/random/src/pcg64/pcg64.h: PCG_DEFAULT_MULTIPLIER_HIGH and _LOW.
PCG_MULTIPLIER = (2549297995355413924 << 64) | 4865540595714422341

INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1


def _uint32_words(n: int) -> list[int]:
    """An integer as little-endian 32-bit words, ``_int_to_uint32_array``."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise SimulationError(f"a seed is a non-negative integer, not {n!r}")
    if n == 0:
        return [0]
    words = []
    while n:
        words.append(n & MASK32)
        n >>= 32
    return words


class SeedSequence:
    """numpy's SeedSequence for integer entropy, with no spawn key."""

    def __init__(self, entropy: int, pool_size: int = DEFAULT_POOL_SIZE) -> None:
        self.entropy = entropy
        self.pool = _mix_entropy(_uint32_words(entropy), pool_size)

    def generate_state(self, n_words: int, bits: int = 32) -> list[int]:
        """``generate_state(n_words, np.uint32)`` or, with ``bits=64``, ``np.uint64``,
        whose words are pairs of 32-bit words read little-endian."""
        if bits not in (32, 64):
            raise SimulationError("state words are 32 or 64 bits")
        count = n_words * (bits // 32)
        hash_const = INIT_B
        words32 = []
        for value in itertools.islice(itertools.cycle(self.pool), count):
            value ^= hash_const
            hash_const = (hash_const * MULT_B) & MASK32
            value = (value * hash_const) & MASK32
            value ^= value >> XSHIFT
            words32.append(value)
        if bits == 32:
            return words32
        return [words32[i] | (words32[i + 1] << 32) for i in range(0, count, 2)]


def _mix_entropy(entropy: list[int], pool_size: int) -> list[int]:
    hash_const = INIT_A

    def hashmix(value: int) -> int:
        nonlocal hash_const
        value ^= hash_const
        hash_const = (hash_const * MULT_A) & MASK32
        value = (value * hash_const) & MASK32
        return value ^ (value >> XSHIFT)

    def mix(x: int, y: int) -> int:
        result = (MIX_MULT_L * x - MIX_MULT_R * y) & MASK32
        return result ^ (result >> XSHIFT)

    pool = [hashmix(entropy[i] if i < len(entropy) else 0) for i in range(pool_size)]
    for i_src in range(pool_size):
        for i_dst in range(pool_size):
            if i_src != i_dst:
                pool[i_dst] = mix(pool[i_dst], hashmix(pool[i_src]))
    for i_src in range(pool_size, len(entropy)):
        for i_dst in range(pool_size):
            pool[i_dst] = mix(pool[i_dst], hashmix(entropy[i_src]))
    return pool


class PCG64:
    """numpy's PCG64 bit generator."""

    def __init__(self, seed: int | SeedSequence) -> None:
        sequence = seed if isinstance(seed, SeedSequence) else SeedSequence(seed)
        words = sequence.generate_state(4, 64)
        initstate = (words[0] << 64) | words[1]
        initseq = (words[2] << 64) | words[3]
        # pcg_setseq_128_srandom_r
        self._inc = ((initseq << 1) | 1) & MASK128
        self._state = 0
        self._step()
        self._state = (self._state + initstate) & MASK128
        self._step()
        self._has_uint32 = False
        self._uinteger = 0

    def _step(self) -> None:
        self._state = (self._state * PCG_MULTIPLIER + self._inc) & MASK128

    def next64(self) -> int:
        """Step, then the XSL-RR output of the new state (``random_raw``)."""
        self._step()
        state = self._state
        value = ((state >> 64) ^ state) & MASK64
        rotation = state >> 122
        return ((value >> rotation) | (value << (-rotation & 63))) & MASK64

    def next32(self) -> int:
        """The low half of a 64-bit output, keeping the high half for the next call."""
        if self._has_uint32:
            self._has_uint32 = False
            return self._uinteger
        value = self.next64()
        self._has_uint32 = True
        self._uinteger = value >> 32
        return value & MASK32

    def next_double(self) -> float:
        return (self.next64() >> 11) * (1.0 / 9007199254740992.0)


class Generator:
    """The two ``numpy.random.Generator`` methods the registered controls call."""

    def __init__(self, bit_generator: PCG64) -> None:
        self.bit_generator = bit_generator

    def random(self, size: int | None = None) -> float | list[float]:
        """``Generator.random()`` or ``Generator.random(size)`` as a list."""
        if size is None:
            return self.bit_generator.next_double()
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SimulationError("random takes a non-negative integer size")
        draw = self.bit_generator.next_double
        return [draw() for _ in range(size)]

    def integers(self, low: int, high: int | None = None, size=None,
                 endpoint: bool = False) -> int:
        """One int64 from [low, high), or [low, high] with ``endpoint``."""
        if size is not None:
            raise SimulationError("only single draws are ported; no control draws arrays")
        if high is None:
            low, high = 0, low
        if not endpoint:
            high -= 1
        if low < INT64_MIN or high > INT64_MAX or low > high:
            raise SimulationError(f"integers bounds {low}, {high} are out of range")
        span = high - low
        if span == 0:
            return low  # numpy draws nothing for a single value
        if span == MASK32:
            return low + self.bit_generator.next32()
        if span > MASK32:
            raise SimulationError("ranges wider than 32 bits are not ported")
        return low + self._lemire32(span)

    def _lemire32(self, span: int) -> int:
        """``buffered_bounded_lemire_uint32``: Lemire's multiply and reject."""
        excluded = span + 1
        draw = self.bit_generator.next32
        product = draw() * excluded
        leftover = product & MASK32
        if leftover < excluded:
            threshold = (MASK32 - span) % excluded
            while leftover < threshold:
                product = draw() * excluded
                leftover = product & MASK32
        return product >> 32


def default_rng(seed: int) -> Generator:
    """``numpy.random.default_rng(seed)`` for a non-negative integer seed."""
    return Generator(PCG64(seed))
