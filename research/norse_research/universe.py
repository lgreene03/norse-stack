"""Which instruments H1's dated universe can select, computed without hindsight.

H1's universe at each month end is the five largest non-stablecoin Binance USDT spot
pairs by 30-day volume, delisted pairs included (docs/NEXT_ITERATION.md). Knowing
which pairs that rule can ever pick needs the daily volume of every USDT pair the
archive holds, so that is what this module ranks. It ranks by quote volume (USDT),
the only volume comparable across coins, over the 30 daily bars that close at the
month end.

To stay robust to details the pre-registration (G0.7) still has to fix, such as the
exact stablecoin list or a minimum history, the candidate set is the union of the top
ten at every month end, not the top five. Any rule that excludes up to five more
pairs than this one still picks only from that set. Only membership is reported,
never a volume figure.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

DAY_MS = 86_400_000
WINDOW_DAYS = 30
CANDIDATE_DEPTH = 10

# Base assets excluded by the "non-stablecoin" rule: coins designed to hold a fiat
# value, and fiat currencies themselves. Gold tokens (PAXG, XAUT) are not pegged to a
# currency and stay in; so do wrapped and staked coins (WBTC, WBETH, BNSOL). G0.7
# fixes the final list; the top-ten depth absorbs up to five further exclusions.
STABLE_OR_FIAT = frozenset(
    {
        # US dollar stablecoins
        "BUSD", "USDC", "TUSD", "PAX", "USDP", "USDS", "USDSB", "USDSOLD", "DAI",
        "FDUSD", "USDE", "USD1", "RLUSD", "BFUSD", "XUSD", "UST", "USTC", "SUSD",
        "FRAX", "RWUSD", "USDEB",
        # Other pegged stablecoins and fiat
        "AEUR", "EURI", "EUR", "GBP", "AUD", "BKRW",
    }
)  # fmt: skip


def base_asset(symbol: str) -> str:
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        raise ValueError(f"{symbol} is not a USDT pair")
    return symbol[:-4]


def is_ranked(symbol: str) -> bool:
    """Whether the universe rule ranks this pair at all (stablecoins and fiat are out)."""
    return base_asset(symbol) not in STABLE_OR_FIAT


def leveraged_bases(bases: Iterable[str]) -> set[str]:
    """Binance leveraged tokens (BTCUP, ETHBEAR, ...): flagged, not excluded."""
    present = set(bases)
    flagged = set()
    for base in present:
        for suffix in ("UP", "DOWN", "BULL", "BEAR"):
            if base.endswith(suffix) and (base[: -len(suffix)] in present or base == suffix):
                flagged.add(base)
    return flagged


def month_close_ms(month: str) -> int:
    """The instant a month's last daily bar closes: 00:00 UTC on the next month's 1st."""
    year, mon = int(month[:4]), int(month[5:])
    days = calendar.monthrange(year, mon)[1]
    start = datetime(year, mon, 1, tzinfo=UTC)
    return int(start.timestamp() * 1000) + days * DAY_MS


def months_between(first: str, last: str) -> list[str]:
    year, mon = int(first[:4]), int(first[5:])
    end = (int(last[:4]), int(last[5:]))
    months = []
    while (year, mon) <= end:
        months.append(f"{year:04d}-{mon:02d}")
        mon += 1
        if mon == 13:
            year, mon = year + 1, 1
    return months


def trailing_volume(daily_quote: Mapping[int, float], close_ms: int) -> float:
    """Quote volume over the 30 daily bars that close at ``close_ms``."""
    start = close_ms - WINDOW_DAYS * DAY_MS
    return sum(daily_quote.get(t, 0.0) for t in range(start, close_ms, DAY_MS))


def rank_month_ends(
    volumes: Mapping[str, Mapping[int, float]], months: Iterable[str], depth: int
) -> dict[str, list[str]]:
    """For each month end, the ``depth`` pairs with the largest 30-day quote volume.

    Ties break by symbol so the result never depends on dictionary order. Pairs with
    no volume in the window are never ranked.
    """
    ranking = {}
    for month in months:
        close = month_close_ms(month)
        scored = []
        for symbol, series in volumes.items():
            vol = trailing_volume(series, close)
            if vol > 0:
                scored.append((-vol, symbol))
        scored.sort()
        ranking[month] = [symbol for _, symbol in scored[:depth]]
    return ranking


def candidate_set(ranking: Mapping[str, list[str]]) -> list[str]:
    return sorted({s for symbols in ranking.values() for s in symbols})
