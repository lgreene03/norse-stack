"""Synthetic markets and resolved configurations for the strategy tests (G2.2, G2.3).

Never real data: every price, volume and funding rate here is either written by hand
or drawn from a seeded ``random.Random``, and nothing opens the dataset store. The
configurations are resolved from the real docs/preregistration.json exactly as the
harness resolves them, with the strategy module's committed declarations, and then
given a synthetic window, so a test runs each configuration's registered parameters.
``listing_world`` writes a synthetic mirror and synthetic development and holdout
manifests into a temporary directory, for the registered loader to list and read.
"""

from __future__ import annotations

import hashlib
import random
from collections import namedtuple
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from conftest import make_zip
from norse_research.formats import DatasetSpec, Funding, Kline
from norse_research.harness import Strategy
from norse_research.manifest import Manifest, build, write
from norse_research.periods import DEVELOPMENT, HOLDOUT
from norse_research.registration import Registration
from norse_research.sources import strategy_declarations
from norse_research.store import ArchiveStore
from norse_research.strategies.h1 import trend
from synthetic_store import make_series

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
# USDC is removed before ranking and ETHBULL is a leveraged token (D14); GMT is too young
# for D13 until its 121st day; HOOK stops trading mid-window (D12); IO misses one daily
# bar mid-window and returns as a new instrument with no history (section 4); PEPE's
# perpetual is quoted per thousand coins (section 4) and funds hot at times (H1-07).
H1_SPEC = {
    "ADAUSDT": (0, None, 9e6, 0.004, 0.03),
    "BNBUSDT": (0, None, 8e6, -0.003, 0.04),
    "CHZUSDT": (0, None, 7e6, 0.002, 0.05),
    "DOTUSDT": (0, None, 6e6, 0.001, 0.02),
    "EOSUSDT": (0, None, 5e6, -0.001, 0.06),
    "FILUSDT": (0, None, 2e6, 0.003, 0.03),
    "GMTUSDT": (110, None, 2e7, 0.005, 0.05),
    "HOOKUSDT": (0, 165, 1.2e7, 0.002, 0.04),
    "IOUSDT": (0, None, 4e6, 0.002, 0.03),
    "PEPEUSDT": (0, None, 6.5e6, 0.003, 0.04),
    "USDCUSDT": (0, None, 5e8, 0.0, 0.0001),
    "ETHBULLUSDT": (0, None, 3e8, 0.0, 0.05),
}
H1_FIRST = ms(2021, 1, 1)
H1_DAYS = 230  # to 2021-08-18
IO_MISSING_DAY = 170  # 2021-06-20: IO's daily bar is missing, so a new instrument starts
H1_FUNDED = {"ADAUSDT": "ADAUSDT", "PEPEUSDT": "1000PEPEUSDT", "CHZUSDT": "CHZUSDT"}


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
            if symbol == "IOUSDT" and i == IO_MISSING_DAY:
                continue
            t = H1_FIRST + i * DAY
            prev = closes[k - 1] if k else close
            quote = base * rng.uniform(0.5, 1.5)
            day_rows.append(bar(t, DAY, close, prev, volume=quote / close, quote=quote,
                                low=min(close, prev) * rng.uniform(0.9, 0.99)))
            for j in range(6):
                if symbol == "ADAUSDT" and i == 185 and j == 2:
                    continue  # a missing four-hour bar: the decision at its close is skipped
                c4 = prev + (close - prev) * (j + 1) / 6 * rng.uniform(0.98, 1.02)
                four_rows.append(bar(t + j * H4, H4, c4, volume=1.0))
            if t >= fill_from and symbol not in ("USDCUSDT", "ETHBULLUSDT"):
                for q in range(96):
                    roll = rng.random()
                    if roll < 0.03:
                        continue  # a missing 15-minute bar delays a fill
                    volume = 0.0 if roll < 0.08 else rng.uniform(5, 15)
                    if symbol == "BNBUSDT" and i in (180, 181):
                        volume = 0.0  # two days without volume: BNB's orders then lapse
                    price = prev + (close - prev) * (q + 1) / 96
                    fill_rows.append(bar(t + q * M15, M15, price, volume=volume))
        rows[f"spot/klines/{symbol}/1d"] = day_rows
        rows[f"spot/klines/{symbol}/4h"] = four_rows
        if symbol not in ("USDCUSDT", "ETHBULLUSDT"):
            rows[f"spot/klines/{symbol}/15m"] = fill_rows
    for symbol, perp in H1_FUNDED.items():
        rate, out = 0.0001, []
        for i in range(H1_DAYS * 3):
            if symbol == "CHZUSDT" and i == 500:
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


class FakeView:
    """Stands in for the runner's ``DataView`` over synthetic rows. ``list_series`` lists
    every series name, sorted, as the runner's lists the manifests'; ``coverage`` gives
    one run per series at the manifests' precision (a bar's open exactly, a settlement's
    minute), and ``load`` serves rows in range; both record what was asked."""

    def __init__(self, rows: dict) -> None:
        self.rows = rows
        self.covered: set[str] = set()
        self.loaded: set[str] = set()

    def list_series(self) -> tuple[str, ...]:
        return tuple(sorted(self.rows))

    @staticmethod
    def _key(series: str):
        return (lambda r: r.calc_time) if "fundingRate" in series else (lambda r: r.open_time)

    def coverage(self, series: str) -> list[tuple[int, int]]:
        rows = self.rows.get(series)
        if not rows:
            raise LookupError(f"{series} is in no manifest this run reads")
        self.covered.add(series)
        first, last = self._key(series)(rows[0]), self._key(series)(rows[-1])
        if "fundingRate" in series:
            first, last = first - first % 60_000, last - last % 60_000
        return [(first, last)]

    def load(self, series, start, end, accept_recorded_gaps=False):
        assert accept_recorded_gaps is True  # section 5, Gaps
        self.loaded.add(series)
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        key = self._key(series)
        rows = [r for r in self.rows[series] if lo <= key(r) < hi]
        return namedtuple("Loaded", "rows gaps")(rows, [])


def listed(rows: dict) -> dict[str, int]:
    """H1's listed pairs for ``rows`` (``trend.listed_pairs`` through a stand-in view):
    each spot daily pair with the open of its first row."""
    view = FakeView(rows)
    return trend.listed_pairs(view, view.list_series())


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


# --- synthetic manifests for H1's listing ---------------------------------------------

# symbol: (first day archived, last day archived or None for 2026-02-28, daily quote
# volume, from 2025-10-01 on, daily drift). Every pair but ZAMA is in the development
# manifest; every pair still trading in 2026 is in the holdout manifest too. USDC is
# removed before ranking and ETHBULL is a leveraged token (D14); DOT's volume rises on
# 2025-10-01; EOS stops trading on 2025-11-15 (D12); LUNC is first archived on
# 2025-09-01, so it has no close 120 days back (D13) until 2026-01-01; ZAMA is first
# archived on 2026-01-01, in the holdout alone; ADABTC is not a USDT pair (section 6).
LISTING_SPEC = {
    "ADAUSDT": ("2025-03-01", None, 9e6, 9e6, 0.004),
    "BNBUSDT": ("2025-03-01", None, 8e6, 8e6, -0.002),
    "CHZUSDT": ("2025-03-01", None, 7e6, 7e6, 0.002),
    "DOTUSDT": ("2025-03-01", None, 6e6, 1.2e7, 0.003),
    "EOSUSDT": ("2025-03-01", "2025-11-15", 5e6, 5e6, 0.001),
    "FILUSDT": ("2025-03-01", None, 4e6, 4e6, 0.002),
    "USDCUSDT": ("2025-03-01", None, 1e9, 1e9, 0.0),
    "ETHBULLUSDT": ("2025-03-01", None, 5e8, 5e8, 0.0),
    "LUNCUSDT": ("2025-09-01", None, 2e7, 2e7, 0.003),
    "ZAMAUSDT": ("2026-01-01", None, 1e8, 1e8, 0.005),
    "ADABTC": ("2025-03-01", None, 1e15, 1e15, 0.0),
}
LISTING_END = ms(2026, 3, 1)
LISTING_FILLS_FROM = ms(2025, 9, 1)
LISTING_FILLED = ("ADAUSDT", "BNBUSDT", "CHZUSDT", "DOTUSDT", "EOSUSDT",
                  "FILUSDT", "LUNCUSDT")


def put_bars(store: ArchiveStore, spec: DatasetSpec, bars: list[tuple[int, float, float]]):
    """Monthly archive files of ``bars`` (open time, price, quote volume), each beside
    its CHECKSUM, as synthetic_store.put writes them; returns the months written."""
    by_month: dict[str, list[str]] = {}
    step = spec.step_ms
    for t, price, quote in bars:
        by_month.setdefault(iso(t)[:7], []).append(
            f"{t},{price},{price},{price},{price},{quote / price},{t + step - 1},{quote},1,"
            f"{quote / price / 2},{quote / 2},0")
    for month, lines in by_month.items():
        data = make_zip(spec.file_name(month)[:-4] + ".csv", "\n".join(lines) + "\n")
        path = store.path(spec.key(month))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        path.with_name(path.name + ".CHECKSUM").write_text(f"{digest}  {path.name}\n")
    return sorted(by_month)


def listing_world(root: Path, seed: int = 7) -> tuple[ArchiveStore, dict[str, Manifest]]:
    """A synthetic mirror of ``LISTING_SPEC``'s daily bars (closes on a seeded random walk,
    flat quote volumes), 15-minute bars from 2025-09-01 for the pairs a universe can hold
    (each priced at its day's close), and one perpetual's daily bars and funding, which
    H1-01 must not read; with a development and a holdout manifest built from it."""
    rng = random.Random(seed)
    store = ArchiveStore(root / "mirror")
    series = []
    for symbol, (first_day, last, quote, quote_from_october, drift) in LISTING_SPEC.items():
        first = ms(*map(int, first_day.split("-")))
        end = LISTING_END if last is None else ms(*map(int, last.split("-"))) + DAY
        daily_bars, fill_bars, close = [], [], 100.0 * (1 + rng.random())
        for t in range(first, end, DAY):
            close *= 1.0 + rng.gauss(drift, 0.03)
            daily_bars.append((t, close, quote if t < ms(2025, 10, 1) else quote_from_october))
            if symbol in LISTING_FILLED and t >= LISTING_FILLS_FROM:
                fill_bars += [(t + q * M15, close, 1e4) for q in range(96)]
        spec = DatasetSpec.from_name(f"spot/klines/{symbol}/1d")
        series.append((spec, put_bars(store, spec, daily_bars)))
        if fill_bars:
            spec = DatasetSpec.from_name(f"spot/klines/{symbol}/15m")
            series.append((spec, put_bars(store, spec, fill_bars)))
    series.extend(make_series(store, name, "2025-09", "2026-02")
                  for name in ("um/klines/ADAUSDT/1d", "um/fundingRate/ADAUSDT"))
    manifests = {}
    for period in (DEVELOPMENT, HOLDOUT):
        path = root / f"{period}.json"
        write(build(store, period, series), path)
        manifests[period] = Manifest.load(path)
    return store, manifests
