"""Canonical JSON for the trial ledger and configuration hashes (G1.5).

One value has exactly one serialisation here, so a hash covers a meaning rather
than a spelling (threat model T4 and T16):

* object keys sorted, no whitespace, ASCII only (``\\uXXXX`` escapes);
* numbers normalised: a float with an integral value below 2^53 in magnitude is
  written as an integer (``1.0`` and ``1`` are one number), any other float in
  Python's shortest round-trip form, and NaN or infinity are refused;
* booleans and ``null`` as JSON spells them.

Parsing is strict: a key that appears twice in one object is refused, because
parsers disagree on which value wins, so a hash would cover a value some readers
never see (T4).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

_EXACT_INT = 2**53
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# A git object id: SHA-1 (40 hex) or SHA-256 (64 hex), never abbreviated.
GIT_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class CanonicalError(ValueError):
    """A value cannot be serialised canonically, or a text is not canonical JSON."""


def normalise(value: Any) -> Any:
    """``value`` with every number in canonical form and every container a plain
    dict or list, so that serialising it is deterministic."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalError(f"{value!r} has no JSON form")
        if value == int(value) and abs(value) < _EXACT_INT:
            return int(value)
        return value
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"object key {key!r} is not a string")
            out[key] = normalise(item)
        return out
    if isinstance(value, Sequence):
        return [normalise(item) for item in value]
    raise CanonicalError(f"{type(value).__name__} has no canonical JSON form")


def dumps(value: Any) -> str:
    """The canonical serialisation of ``value``, as text."""
    return json.dumps(
        normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return dumps(value).encode("ascii")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def value_hash(value: Any) -> str:
    """Full SHA-256 of the canonical serialisation of ``value``."""
    return sha256_hex(canonical_bytes(value))


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise CanonicalError(f"key {key!r} appears twice in one object")
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise CanonicalError(f"{name} is not JSON")


def loads_strict(text: str | bytes) -> Any:
    """Parse JSON, refusing repeated keys and NaN or infinity."""
    try:
        return json.loads(
            text, object_pairs_hook=_refuse_duplicates, parse_constant=_refuse_constant
        )
    except json.JSONDecodeError as exc:
        raise CanonicalError(f"not JSON: {exc}") from None


def loads_canonical(text: str) -> Any:
    """Parse one canonical JSON text, refusing any text that differs from the
    canonical serialisation of what it parses to."""
    value = loads_strict(text)
    if dumps(value) != text:
        raise CanonicalError("the text differs from its canonical serialisation")
    return value
