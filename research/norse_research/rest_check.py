"""Check archive klines against Binance's public REST API (goal G1.1).

Uses ``data-api.binance.vision``, Binance's public market-data-only host: no key, no
account, and no order or account endpoint exists on it. Only closed klines are
compared, because the bar still forming changes until it closes.
"""

from __future__ import annotations

import json
import urllib.parse

from .binance_archive import Fetch
from .formats import DataIntegrityError, Kline

REST_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
REST_MAX_BYTES = 1_000_000
REST_LIMIT = 1000


def rest_url(symbol: str, interval: str, start_ms: int, end_ms: int) -> str:
    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms - 1,
            "limit": REST_LIMIT,
        }
    )
    return f"{REST_KLINES_URL}?{query}"


def parse_rest_klines(body: bytes) -> list[Kline]:
    """The REST API's kline arrays as Kline rows, parsed exactly as archive rows are."""
    rows = []
    for item in json.loads(body):
        if not isinstance(item, list) or len(item) != 12:
            raise DataIntegrityError(f"unexpected REST kline {item!r:.120}")
        rows.append(
            Kline(
                int(item[0]),
                float(item[1]),
                float(item[2]),
                float(item[3]),
                float(item[4]),
                float(item[5]),
                int(item[6]),
                float(item[7]),
                int(item[8]),
                float(item[9]),
                float(item[10]),
            )
        )
    return rows


def fetch_rest_klines(
    symbol: str, interval: str, start_ms: int, end_ms: int, fetch: Fetch, now_ms: int
) -> list[Kline]:
    """Closed REST klines with open time in [start, end), at most 1,000 of them."""
    rows = parse_rest_klines(fetch(rest_url(symbol, interval, start_ms, end_ms), REST_MAX_BYTES))
    return [r for r in rows if start_ms <= r.open_time < end_ms and r.close_time < now_ms]


def compare(archive: list[Kline], rest: list[Kline]) -> list[str]:
    """Differences between archive and REST rows over the REST rows' span, matched by
    open time; empty if every bar is present on both sides and equal field by field."""
    if not rest:
        return ["the REST API returned no closed klines"]
    lo, hi = rest[0].open_time, rest[-1].open_time
    ours = {r.open_time: r for r in archive if lo <= r.open_time <= hi}
    theirs = {r.open_time: r for r in rest}
    problems = []
    for t in sorted(ours.keys() | theirs.keys()):
        if t not in ours:
            problems.append(f"bar at {t}: in the REST API, not the archive")
        elif t not in theirs:
            problems.append(f"bar at {t}: in the archive, not the REST API")
        elif ours[t] != theirs[t]:
            fields = [f for f in Kline._fields if getattr(ours[t], f) != getattr(theirs[t], f)]
            problems.append(f"bar at {t}: {', '.join(fields)} differ")
    return problems
