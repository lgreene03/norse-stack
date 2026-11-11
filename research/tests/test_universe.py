from datetime import UTC, datetime

import pytest
from norse_research.universe import (
    DAY_MS,
    base_asset,
    candidate_set,
    is_ranked,
    leveraged_bases,
    month_close_ms,
    months_between,
    rank_month_ends,
    trailing_volume,
)


def ms(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp() * 1000)


def test_month_close_is_midnight_on_the_next_first():
    assert month_close_ms("2024-01") == ms(2024, 2, 1)
    assert month_close_ms("2024-02") == ms(2024, 3, 1)  # leap year
    assert month_close_ms("2023-12") == ms(2024, 1, 1)


def test_months_between_crosses_years():
    assert months_between("2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]


def test_trailing_volume_uses_exactly_the_30_bars_closing_at_month_end():
    close = month_close_ms("2024-01")
    series = {close - d * DAY_MS: 1.0 for d in range(1, 31)}  # the 30 bars in the window
    series[close - 31 * DAY_MS] = 100.0  # one bar too early
    series[close] = 100.0  # the next month's first bar
    assert trailing_volume(series, close) == 30.0


def test_rank_month_ends_orders_by_volume_breaks_ties_by_symbol_and_skips_zero():
    close = month_close_ms("2024-01")
    bar = close - DAY_MS
    volumes = {
        "AUSDT": {bar: 5.0},
        "BUSDT": {bar: 9.0},
        "CUSDT": {bar: 5.0},
        "DUSDT": {bar: 0.0},
        "EUSDT": {},
    }
    ranking = rank_month_ends(volumes, ["2024-01"], depth=3)
    assert ranking == {"2024-01": ["BUSDT", "AUSDT", "CUSDT"]}
    assert rank_month_ends(volumes, ["2024-01"], depth=10)["2024-01"] == [
        "BUSDT",
        "AUSDT",
        "CUSDT",
    ]


def test_candidate_set_is_the_union_over_month_ends():
    assert candidate_set({"a": ["X", "Y"], "b": ["Y", "Z"]}) == ["X", "Y", "Z"]


def test_stablecoins_and_fiat_are_not_ranked_but_gold_and_wrapped_coins_are():
    assert not is_ranked("USDCUSDT")
    assert not is_ranked("FDUSDUSDT")
    assert not is_ranked("EURUSDT")
    assert is_ranked("BTCUSDT")
    assert is_ranked("PAXGUSDT")
    assert is_ranked("WBTCUSDT")
    with pytest.raises(ValueError):
        base_asset("BTCBUSD")


def test_leveraged_tokens_are_flagged_without_catching_real_coins():
    bases = {"BTC", "BTCUP", "BTCDOWN", "XRP", "XRPBEAR", "BULL", "JUP", "SUPER"}
    assert leveraged_bases(bases) == {"BTCUP", "BTCDOWN", "XRPBEAR", "BULL"}
