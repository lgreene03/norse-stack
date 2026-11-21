"""Synthetic markets and resolved configurations for the strategy tests (G2.2, G2.3).

Never real data: every price, volume and funding rate here is either written by hand
or drawn from a seeded ``random.Random``, and nothing opens the dataset store. The
configurations are resolved from the real docs/preregistration.json exactly as the
harness resolves them, with the strategy module's committed declarations, and then
given a synthetic window, so a test runs each configuration's registered parameters.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from norse_research.formats import Funding, Kline
from norse_research.harness import Strategy
from norse_research.registration import Registration
from norse_research.sources import strategy_declarations

RESEARCH = Path(__file__).resolve().parent.parent
REPO = RESEARCH.parent
STRATEGIES = RESEARCH / "norse_research" / "strategies"
MODULES = {"H1": "research/norse_research/strategies/h1/trend.py",
           "H2": "research/norse_research/strategies/h2/carry.py"}
REGISTRATION = Registration.from_bytes((REPO / "docs" / "preregistration.json").read_bytes())

DAY = 86_400_000
H4 = 14_400_000
M15 = 900_000
HOUR = 3_600_000


def ms(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=UTC).timestamp() * 1000)


def iso(t):
    return datetime.fromtimestamp(t / 1000, tz=UTC).strftime("%Y-%m-%d")


# --- configurations -------------------------------------------------------------------


def declared(hypothesis: str) -> Strategy:
    """The strategy as the harness reads it: its pinned path and the PARAMETERS and
    DEFAULTS its committed source declares (never executed for this)."""
    path = MODULES[hypothesis]
    _, parameters, defaults = strategy_declarations((REPO / path).read_text(), "run")
    return Strategy(f"{path}:run", None, parameters, defaults)


def configuration(cid: str, first_day: str, last_day: str, seed=None,
                  kind_override=None) -> dict:
    """``cid`` resolved against the real registration, over a synthetic window."""
    hypothesis = REGISTRATION.hypothesis_of(
        cid if cid in REGISTRATION.grid else
        REGISTRATION.controls[cid]["base"] if cid in REGISTRATION.controls else
        {"B-H1": "H1-01", "B-H2": "H2-01"}[cid])
    request = {"configuration": cid}
    if seed is not None:
        request["seed"] = seed
    resolved = REGISTRATION.resolve(request, declared(hypothesis)).configuration
    days = (date.fromisoformat(last_day) - date.fromisoformat(first_day)).days + 1
    out = {**resolved, "window": {"first_return_day": first_day, "last_return_day": last_day,
                                  "days": days}}
    if kind_override is not None:
        out["kind"] = kind_override
    return out


# --- bars -----------------------------------------------------------------------------


def bar(open_time, step, close, prev=None, volume=1.0, quote=None, low=None, high=None):
    prev = close if prev is None else prev
    quote = volume * close if quote is None else quote
    high = max(close, prev) if high is None else high
    low = min(close, prev) if low is None else low
    return Kline(open_time, prev, high, low, close, volume, open_time + step - 1, quote, 1,
                 volume / 2, quote / 2)


def daily(first, closes, quotes=None, lows=None):
    """One daily bar per close from ``first``; quote volume 1,000 a day unless given."""
    out = []
    prev = closes[0]
    for i, close in enumerate(closes):
        quote = 1000.0 if quotes is None else quotes[i]
        low = None if lows is None else lows[i]
        out.append(bar(first + i * DAY, DAY, close, prev, volume=quote / close, quote=quote,
                       low=low))
        prev = close
    return out


def geometric(start, returns):
    """Closes compounding ``returns`` from ``start``."""
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


def fills(first, last, price, volume=10.0):
    """15-minute bars opening in [first, last) at a constant VWAP ``price``."""
    return [bar(t, M15, price, volume=volume) for t in range(first, last, M15)]


def settlements(first, rates, jitter=None):
    """Funding settled every eight hours from ``first``, one rate each."""
    jitter = jitter or (lambda i: 0)
    return [Funding(first + i * 8 * HOUR + jitter(i), 8, rate) for i, rate in enumerate(rates)]


# --- a seeded market for H1 -----------------------------------------------------------

# symbol: (first day index, last day index or None, base quote volume, drift, sigma).
# USDC is removed before ranking and ETHBULL is a leveraged token (D14); GGG is too young
# for D13 until its 121st day; HHH stops trading mid-window (D12); III misses one daily
# bar mid-window and returns as a new instrument with no history (section 4); KKK's
# perpetual is quoted per thousand coins (section 4) and funds hot at times (H1-07).
H1_SPEC = {
    "AAAUSDT": (0, None, 9e6, 0.004, 0.03),
    "BBBUSDT": (0, None, 8e6, -0.003, 0.04),
    "CCCUSDT": (0, None, 7e6, 0.002, 0.05),
    "DDDUSDT": (0, None, 6e6, 0.001, 0.02),
    "EEEUSDT": (0, None, 5e6, -0.001, 0.06),
    "FFFUSDT": (0, None, 2e6, 0.003, 0.03),
    "GGGUSDT": (110, None, 2e7, 0.005, 0.05),
    "HHHUSDT": (0, 165, 1.2e7, 0.002, 0.04),
    "IIIUSDT": (0, None, 4e6, 0.002, 0.03),
    "KKKUSDT": (0, None, 6.5e6, 0.003, 0.04),
    "USDCUSDT": (0, None, 5e8, 0.0, 0.0001),
    "ETHBULLUSDT": (0, None, 3e8, 0.0, 0.05),
}
H1_FIRST = ms(2021, 1, 1)
H1_DAYS = 230  # to 2021-08-18
III_MISSING_DAY = 170  # 2021-06-20: III's daily bar is missing, so a new instrument starts
H1_FUNDED = {"AAAUSDT": "AAAUSDT", "KKKUSDT": "1000KKKUSDT", "CCCUSDT": "CCCUSDT"}


def h1_market(seed: int = 11, fill_from: int = ms(2021, 5, 25)) -> dict:
    """Daily and four-hour bars from 2021-01-01, 15-minute bars from ``fill_from``, and
    funding for three perpetuals, by series name."""
    rng = random.Random(seed)
    rows: dict[str, list] = {}
    for symbol, (start, stop, base, drift, sigma) in H1_SPEC.items():
        last = H1_DAYS if stop is None else stop + 1
        closes = [100.0 * (1 + rng.random())]
        for _ in range(start + 1, last):
            closes.append(max(1e-6, closes[-1] * (1.0 + rng.gauss(drift, sigma))))
        day_rows, four_rows, fill_rows = [], [], []
        for k, close in enumerate(closes):
            i = start + k
            if symbol == "IIIUSDT" and i == III_MISSING_DAY:
                continue
            t = H1_FIRST + i * DAY
            prev = closes[k - 1] if k else close
            quote = base * rng.uniform(0.5, 1.5)
            day_rows.append(bar(t, DAY, close, prev, volume=quote / close, quote=quote,
                                low=min(close, prev) * rng.uniform(0.9, 0.99)))
            for j in range(6):
                if symbol == "AAAUSDT" and i == 185 and j == 2:
                    continue  # a missing four-hour bar: the decision at its close is skipped
                c4 = prev + (close - prev) * (j + 1) / 6 * rng.uniform(0.98, 1.02)
                four_rows.append(bar(t + j * H4, H4, c4, volume=1.0))
            if t >= fill_from and symbol not in ("USDCUSDT", "ETHBULLUSDT"):
                for q in range(96):
                    roll = rng.random()
                    if roll < 0.03:
                        continue  # a missing 15-minute bar delays a fill
                    volume = 0.0 if roll < 0.08 else rng.uniform(5, 15)
                    if symbol == "BBBUSDT" and i in (180, 181):
                        volume = 0.0  # two days without volume: BBB's orders then lapse
                    price = prev + (close - prev) * (q + 1) / 96
                    fill_rows.append(bar(t + q * M15, M15, price, volume=volume))
        rows[f"spot/klines/{symbol}/1d"] = day_rows
        rows[f"spot/klines/{symbol}/4h"] = four_rows
        if symbol not in ("USDCUSDT", "ETHBULLUSDT"):
            rows[f"spot/klines/{symbol}/15m"] = fill_rows
    for symbol, perp in H1_FUNDED.items():
        rate, out = 0.0001, []
        for i in range(H1_DAYS * 3):
            if symbol == "CCCUSDT" and i == 500:
                continue  # a missing settlement: the filter does not apply across it
            rate = max(-0.0005, min(0.0009, rate + rng.gauss(0, 0.00015)))
            out.append(Funding(H1_FIRST + i * 8 * HOUR + rng.randint(0, 9), 8, rate))
        rows[f"um/fundingRate/{perp}"] = out
    return rows


# --- a seeded market for H2 -----------------------------------------------------------

H2_FIRST = ms(2021, 1, 1)
H2_DAYS = 100  # to 2021-04-10
PERP_GAP_DAY = 70  # 2021-03-12: no perpetual 15-minute or daily bars (section 5, item 6)


def h2_market(seed: int = 5, fill_from: int = ms(2021, 1, 25)) -> dict:
    """BTC and ETH spot and perpetual bars and funding from 2021-01-01, by series name."""
    rng = random.Random(seed)
    rows: dict[str, list] = {}
    for coin, start, sigma in (("BTCUSDT", 30000.0, 0.03), ("ETHUSDT", 1000.0, 0.04)):
        closes = [start]
        for _ in range(H2_DAYS - 1):
            closes.append(closes[-1] * (1.0 + rng.gauss(0.001, sigma)))
        spot_day, perp_day, spot_fill, perp_fill = [], [], [], []
        for i, close in enumerate(closes):
            t = H2_FIRST + i * DAY
            prev = closes[i - 1] if i else close
            basis = 1.0 + rng.uniform(0.0, 0.002)
            spot_day.append(bar(t, DAY, close, prev, volume=1e3, quote=1e3 * close))
            if i != PERP_GAP_DAY:
                perp_day.append(bar(t, DAY, close * basis, prev * basis, volume=1e3))
            if t < fill_from:
                continue
            for q in range(96):
                price = prev + (close - prev) * (q + 1) / 96
                roll = rng.random()
                if roll > 0.02:
                    volume = 0.0 if roll < 0.06 else rng.uniform(5, 15)
                    if q == 0 and i % 2 == 0:
                        volume = 0.0  # on even days a 00:00 decision's fill waits a bar
                    spot_fill.append(bar(t + q * M15, M15, price, volume=volume))
                if i != PERP_GAP_DAY:
                    perp_fill.append(bar(t + q * M15, M15, price * basis,
                                         volume=0.0 if rng.random() < 0.03 else 10.0))
        rows[f"spot/klines/{coin}/1d"] = spot_day
        rows[f"spot/klines/{coin}/15m"] = spot_fill
        rows[f"um/klines/{coin}/1d"] = perp_day
        rows[f"um/klines/{coin}/15m"] = perp_fill
        rate, funding = 0.0001, []
        for i in range(H2_DAYS * 3):
            rate = max(-0.0003, min(0.0006, rate + rng.gauss(0, 0.00008)))
            funding.append(Funding(H2_FIRST + i * 8 * HOUR + rng.randint(0, 9), 8, rate))
        rows[f"um/fundingRate/{coin}"] = funding
    return rows


def truncated(rows: dict, end: int) -> dict:
    """Every row stamped before ``end``: what a run whose data stops there would read,
    as a development run's data stops at 2026-01-01 (section 11, item 5)."""
    out = {}
    for name, series in rows.items():
        out[name] = [r for r in series if (r.calc_time if isinstance(r, Funding)
                                           else r.open_time) < end]
    return out


def last_day(first_day: str, days: int) -> str:
    return (date.fromisoformat(first_day) + timedelta(days=days - 1)).isoformat()
