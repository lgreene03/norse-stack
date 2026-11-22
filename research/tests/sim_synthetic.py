"""Deterministic synthetic markets for the simulation engine's tests (G1.9).

Never real data: prices are a seeded random walk. The market carries what the engine
must handle: 15-minute bars with no volume and missing ones, a pair that stops trading
mid-window (section 4), a pair that stops and later returns as a new instrument, ten
days on which two pairs trade no volume, so orders lapse (section 5, Fills), a
missing four-hour bar (section 5, Gaps item 2), a perpetual with funding settled every
eight hours with millisecond jitter and one missing settlement, and a missing day of
perpetual klines (section 5, Gaps item 6).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from norse_research.formats import Funding, Kline

DAY = 86_400_000
H4 = 14_400_000
M15 = 900_000
DEAD = (50, 60)  # days of AAA's and BTC's spot 15-minute bars with no volume


def ms(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=UTC).timestamp() * 1000)


def _bar(open_time, step, close, prev, volume):
    high, low = max(close, prev) * 1.01, min(close, prev) * 0.99
    quote = volume * (close + prev) / 2
    return Kline(open_time, prev, high, low, close, volume, open_time + step - 1, quote, 5,
                 volume / 2, quote / 2)


def _walk(rng, start, n, sigma):
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + rng.gauss(0.0005, sigma)))
    return prices


FIRST = ms(2021, 1, 1)


def market(seed: int = 7, first: int = FIRST, days: int = 120):
    """Rows by series name; daily bars from ``first`` for ``days`` days."""
    rng = random.Random(seed)
    rows: dict[str, list] = {}
    minutes = days * 96
    symbols = {
        "AAAUSDT": (100.0, 0.0008, None),  # trades throughout
        "BBBUSDT": (20.0, 0.0012, (first + 70 * DAY, None)),  # stops after day 70
        "CCCUSDT": (5.0, 0.0010, (first + 40 * DAY, first + 46 * DAY)),  # stops, returns
        "BTCUSDT": (30000.0, 0.0007, None),
    }
    for symbol, (start, sigma, cut) in symbols.items():
        fine = _walk(rng, start, minutes, sigma)

        def alive(t, cut=cut):
            if cut is None:
                return True
            stop, back = cut
            return t < stop or (back is not None and t >= back)

        m15 = []
        for i, price in enumerate(fine):
            t = first + i * M15
            if not alive(t):
                continue
            roll = rng.random()
            if roll < 0.03:
                continue  # a missing 15-minute bar delays a fill
            volume = 0.0 if roll < 0.08 else rng.uniform(50, 150)
            if symbol in ("AAAUSDT", "BTCUSDT") and DEAD[0] <= (t - first) // DAY < DEAD[1]:
                volume = 0.0  # days with no volume at all: orders decided then lapse
            m15.append(_bar(t, M15, price, fine[i - 1] if i else price, volume))
        rows[f"spot/klines/{symbol}/15m"] = m15
        for name, step in ((f"spot/klines/{symbol}/4h", H4), (f"spot/klines/{symbol}/1d", DAY)):
            per = step // M15
            series = []
            for k in range(minutes // per):
                t = first + k * step
                if not alive(t) or not alive(t + step - M15):
                    continue
                if step == H4 and symbol == "AAAUSDT" and k == 6 * 60 + 2:
                    continue  # one missing four-hour bar on day 60: that decision skips
                close = fine[(k + 1) * per - 1]
                prev = fine[k * per - 1] if k else fine[0]
                series.append(_bar(t, step, close, prev, rng.uniform(1e3, 2e3)))
            rows[name] = series

    # BTC's perpetual: 15-minute and daily bars a touch above spot, and funding.
    spot15 = {b.open_time: b for b in rows["spot/klines/BTCUSDT/15m"]}
    gap_day = first + 90 * DAY  # perpetual klines missing all day (section 5, item 6)
    perp15 = []
    for i in range(minutes):
        t = first + i * M15
        if gap_day <= t < gap_day + DAY:
            continue
        base = spot15.get(t)
        price = (base.close if base else 30000.0) * (1 + rng.uniform(0, 0.0004))
        volume = 0.0 if rng.random() < 0.04 else rng.uniform(50, 150)
        perp15.append(_bar(t, M15, price, price, volume))
    rows["um/klines/BTCUSDT/15m"] = perp15
    daily = []
    for k in range(days):
        t = first + k * DAY
        if t == gap_day:
            continue
        inside = [b for b in perp15 if t <= b.open_time < t + DAY]
        daily.append(_bar(t, DAY, inside[-1].close, inside[0].open, 1e4))
    rows["um/klines/BTCUSDT/1d"] = daily
    funding = []
    drift = 0.0001
    for k in range(days * 3):
        if k == 150:
            continue  # one missing settlement accrues nothing
        drift = max(-0.0005, min(0.0015, drift + rng.gauss(0, 0.0002)))
        funding.append(Funding(first + k * 8 * 3_600_000 + rng.randint(0, 9), 8, drift))
    rows["um/fundingRate/BTCUSDT"] = funding
    return rows
