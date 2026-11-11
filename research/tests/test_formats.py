import pytest
from norse_research.formats import (
    INTERVAL_MS,
    DataIntegrityError,
    DatasetSpec,
    Funding,
    Gap,
    bar_gaps,
    collapse_repeats,
    funding_gaps,
    parse_funding,
    parse_klines,
    read_csv_member,
    to_ms,
)

DAY = INTERVAL_MS["1d"]
HOUR = 3_600_000
T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC


def kline_line(open_ms, close_ms=None, scale=1):
    close_ms = open_ms + DAY - 1 if close_ms is None else close_ms
    return f"{open_ms * scale},1.0,2.0,0.5,1.5,10.0,{close_ms * scale + (scale - 1)},15.0,7,4.0,6.0,0"


def test_spot_microsecond_timestamps_are_normalised_to_ms():
    # From 2025-01 spot files carry microseconds: 1735689600000000.
    text = kline_line(1_735_689_600_000, scale=1000)
    (row,) = parse_klines(text, "t")
    assert row.open_time == 1_735_689_600_000
    assert row.close_time == 1_735_689_600_000 + DAY - 1
    assert to_ms(1_735_689_600_000) == 1_735_689_600_000


def test_header_accepted_only_when_exact_and_first():
    header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
    header += "taker_buy_volume,taker_buy_quote_volume,ignore"
    rows = parse_klines(header + "\n" + kline_line(T0) + "\n", "t")
    assert [r.open_time for r in rows] == [T0]
    with pytest.raises(DataIntegrityError, match="header"):
        parse_klines(header.replace("close_time", "closetime") + "\n" + kline_line(T0), "t")
    with pytest.raises(DataIntegrityError):
        parse_klines(kline_line(T0) + "\n" + header, "t")


def test_wrong_column_count_and_bad_numbers_raise():
    with pytest.raises(DataIntegrityError, match="columns"):
        parse_klines("1,2,3", "t")
    with pytest.raises(DataIntegrityError):
        parse_klines(kline_line(T0).replace("1.5", "x"), "t")


def test_funding_parses_and_refuses_unknown_interval():
    text = "calc_time,funding_interval_hours,last_funding_rate\n1577836800000,8,0.0001\n"
    assert parse_funding(text, "t") == [Funding(1_577_836_800_000, 8, 0.0001)]
    with pytest.raises(DataIntegrityError, match="interval"):
        parse_funding("1577836800000,3,0.0001", "t")


def test_bar_gaps_counts_missing_bars():
    times = [T0, T0 + DAY, T0 + 4 * DAY, T0 + 5 * DAY]
    assert bar_gaps(times, DAY) == [Gap(T0 + DAY, T0 + 4 * DAY, 2)]
    assert bar_gaps([T0, T0 + DAY], DAY) == []


def test_bar_gaps_refuses_duplicates_disorder_and_off_grid_times():
    with pytest.raises(DataIntegrityError, match="increasing"):
        bar_gaps([T0, T0], DAY)
    with pytest.raises(DataIntegrityError, match="increasing"):
        bar_gaps([T0 + DAY, T0], DAY)
    with pytest.raises(DataIntegrityError, match="grid"):
        bar_gaps([T0 + 1], DAY)


def test_exact_repeats_collapse_and_are_reported_but_conflicts_raise():
    # The archive repeats AXSUSDT's 2026-02-10 daily row, identically, in one file.
    text = "\n".join([kline_line(T0), kline_line(T0 + DAY), kline_line(T0 + DAY)])
    rows, repeated = collapse_repeats(parse_klines(text, "t"), "t")
    assert [r.open_time for r in rows] == [T0, T0 + DAY]
    assert repeated == [T0 + DAY]
    conflict = parse_klines(kline_line(T0) + "\n" + kline_line(T0).replace("15.0", "16.0"), "t")
    with pytest.raises(DataIntegrityError, match="different rows"):
        collapse_repeats(conflict, "t")


def test_funding_gaps_tolerate_jitter_and_interval_changes():
    events = [
        Funding(T0, 8, 0.0),
        Funding(T0 + 8 * HOUR + 2, 8, 0.0),  # milliseconds of jitter, as published
        Funding(T0 + 12 * HOUR + 1, 4, 0.0),  # interval moved from 8 to 4 hours
        Funding(T0 + 16 * HOUR, 4, 0.0),
    ]
    assert funding_gaps(events) == []


def test_funding_gaps_find_missing_settlements():
    events = [Funding(T0, 8, 0.0), Funding(T0 + 24 * HOUR, 8, 0.0)]
    assert funding_gaps(events) == [Gap(T0, T0 + 24 * HOUR, 2)]
    with pytest.raises(DataIntegrityError):
        funding_gaps([Funding(T0, 8, 0.0), Funding(T0, 8, 0.0)])


def test_read_csv_member_refuses_anything_but_the_expected_single_csv(zip_of):
    good = zip_of("X-1d-2024-01.csv", "a")
    assert read_csv_member(good, "X-1d-2024-01.csv", 100) == "a"
    with pytest.raises(DataIntegrityError, match="expected only"):
        read_csv_member(good, "Y-1d-2024-01.csv", 100)
    with pytest.raises(DataIntegrityError, match="exceeds cap"):
        read_csv_member(zip_of("X-1d-2024-01.csv", "a" * 101), "X-1d-2024-01.csv", 100)
    with pytest.raises(DataIntegrityError, match="not a zip"):
        read_csv_member(b"not a zip", "X-1d-2024-01.csv", 100)


def test_dataset_spec_keys_round_trip():
    spot = DatasetSpec("spot", "klines", "BTCUSDT", "4h")
    assert spot.key("2024-01") == "data/spot/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-01.zip"
    assert spot.month_of(spot.key("2024-01")) == "2024-01"
    funding = DatasetSpec("um", "fundingRate", "BTCUSDT")
    assert funding.key("2020-01") == (
        "data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2020-01.zip"
    )
    premium = DatasetSpec.from_name("um/premiumIndexKlines/ETHUSDT/1d")
    assert premium.prefix == "data/futures/um/monthly/premiumIndexKlines/ETHUSDT/1d/"
    assert DatasetSpec("spot", "klines", "币安人生USDT", "1d").name == "spot/klines/币安人生USDT/1d"
    with pytest.raises(ValueError):
        spot.month_of("data/spot/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-01-02.zip")


@pytest.mark.parametrize(
    "args",
    [
        ("spot", "fundingRate", "BTCUSDT", None),
        ("um", "fundingRate", "BTCUSDT", "1d"),
        ("um", "klines", "BTCUSDT", None),
        ("spot", "klines", "BTC/../USDT", "1d"),
        ("coinm", "klines", "BTCUSD", "1d"),
        ("spot", "klines", "BTCUSDT", "7m"),
    ],
)
def test_dataset_spec_refuses_bad_combinations(args):
    with pytest.raises(ValueError):
        DatasetSpec(*args)


def test_size_caps_follow_the_interval():
    daily = DatasetSpec("spot", "klines", "BTCUSDT", "1d")
    quarter = DatasetSpec("spot", "klines", "BTCUSDT", "15m")
    assert daily.max_rows() == 31
    assert quarter.max_rows() == 31 * 96
    assert daily.max_bytes() < quarter.max_bytes()
