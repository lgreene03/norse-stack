"""The iteration's fixed data periods (docs/NEXT_ITERATION.md).

Development data ends where the holdout begins. The holdout, 2026-01-01 to
2026-10-31 inclusive, is sealed: until G1.6 builds the unseal, nothing in this
package hands holdout rows to research code, and its files are kept off disk
(docs/research/ledger-threat-model.md, option B), so no tool here fetches a month
after the development period.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
    """Holdout data was requested before the unseal exists (G1.6)."""


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
