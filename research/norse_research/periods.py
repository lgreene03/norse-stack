"""The iteration's fixed data periods (docs/NEXT_ITERATION.md).

Development data ends where the holdout begins. The holdout, 2026-01-01 to
2026-10-31 inclusive, is sealed: nothing in this package hands holdout rows to
research code outside the one holdout run the ledger ties to the unseal entry (G1.6,
``norse_research.seal``), and its files are kept off disk until the unseal
(docs/research/ledger-threat-model.md, option B), so no tool here fetches a month
after the development period; only the seal's re-fetch does, after the unseal.

The package's readers of mirrored files refuse a holdout month unless the manifest
build and verify code passes ``holdout_audit=True``, or the read happens inside the
unsealed holdout run, which only the runner enters (threat model T21).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

# The first return day of any registered out-of-sample window (H1's, from 2018-05-01;
# docs/preregistration.json). Rows from here on load only inside an open ledger entry
# (the loader's speed bump, threat model T2); the harness checks this against the
# registration the genesis entry pins.
OUT_OF_SAMPLE_START = datetime(2018, 5, 1, tzinfo=UTC)
OUT_OF_SAMPLE_START_MS = int(OUT_OF_SAMPLE_START.timestamp() * 1000)

HOLDOUT_START = datetime(2026, 1, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2026, 11, 1, tzinfo=UTC)  # exclusive: the holdout ends 2026-10-31
HOLDOUT_START_MS = int(HOLDOUT_START.timestamp() * 1000)
HOLDOUT_END_MS = int(HOLDOUT_END.timestamp() * 1000)

LAST_DEVELOPMENT_MONTH = "2025-12"
FIRST_HOLDOUT_MONTH = "2026-01"
LAST_HOLDOUT_MONTH = "2026-10"

DEVELOPMENT = "development"
HOLDOUT = "holdout"
AFTER = "after"


class HoldoutSealed(PermissionError):
    """Holdout data was requested outside the unsealed holdout run (G1.6)."""


# The unseal entry the current holdout run belongs to; set only by the runner, inside
# a holdout run the harness has tied to the unseal entry on the remote's ledger.
_UNSEALED_RUN: ContextVar[str | None] = ContextVar("norse_unsealed_holdout_run", default=None)


@contextmanager
def inside_unsealed_run(unseal_entry: str) -> Iterator[None]:
    """Holdout reads in this block belong to the holdout run of the unseal entry
    ``unseal_entry`` (its full SHA-256). Only the runner enters it, after the harness
    has pushed the run's open entry and found the unseal entry on the remote."""
    if not isinstance(unseal_entry, str) or len(unseal_entry) != 64:
        raise ValueError("an unseal entry is named by its full SHA-256")
    token = _UNSEALED_RUN.set(unseal_entry)
    try:
        yield
    finally:
        _UNSEALED_RUN.reset(token)


def unsealed_run() -> str | None:
    """The unseal entry of the holdout run this code runs inside, or None."""
    return _UNSEALED_RUN.get()


def is_holdout_month(month: str) -> bool:
    return FIRST_HOLDOUT_MONTH <= month <= LAST_HOLDOUT_MONTH


def require_holdout_access(what: str, holdout_audit: bool) -> None:
    """Refuse a read of holdout data unless it is the manifest tools' audit or it
    happens inside the unsealed holdout run (T21)."""
    if not holdout_audit and unsealed_run() is None:
        raise HoldoutSealed(
            f"{what} is holdout data, readable only inside the holdout run the ledger ties "
            "to the unseal entry (G1.6), or by the manifest build and verify code"
        )


def period_of_month(month: str) -> str:
    """Which period a monthly archive file belongs to. Months never straddle a boundary."""
    if month <= LAST_DEVELOPMENT_MONTH:
        return DEVELOPMENT
    if month <= LAST_HOLDOUT_MONTH:
        return HOLDOUT
    return AFTER


def iso_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def iso_time(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")
