from collections import namedtuple

import numpy as np
import pandas as pd
import pytest

from mt5_data_fmt import (
    as_float32,
    deals_to_df,
    find_gaps,
    rates_to_df,
    read_parquet,
    resample_ohlc,
    ticks_to_df,
    to_parquet,
)

RATE_DTYPE = [
    ("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
    ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8"),
]
TICK_DTYPE = [
    ("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("volume", "<u8"),
    ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8"),
]
T0 = int(pd.Timestamp("2026-03-02 10:00", tz="UTC").timestamp())


def make_rates(n=6, step=60, start=T0):
    arr = np.zeros(n, dtype=RATE_DTYPE)
    for i in range(n):
        arr[i] = (start + i * step, 1.0 + i, 1.5 + i, 0.5 + i, 1.2 + i, 10 + i, 2 + i, 0)
    return arr


def test_rates_to_df_utc_index_and_columns():
    df = rates_to_df(make_rates())
    assert str(df.index.tz) == "UTC" and df.index.name == "time"
    assert list(df.columns[:4]) == ["open", "high", "low", "close"]
    assert df.index[0] == pd.Timestamp("2026-03-02 10:00", tz="UTC")


def test_server_offset_is_subtracted_to_get_real_utc():
    df = rates_to_df(make_rates(), server_utc_offset_hours=3)
    assert df.index[0] == pd.Timestamp("2026-03-02 07:00", tz="UTC")


def test_rates_duplicates_removed_and_sorted():
    arr = make_rates(3)
    arr = np.concatenate([arr[::-1], arr[:1]])
    df = rates_to_df(arr)
    assert df.index.is_monotonic_increasing and df.index.is_unique and len(df) == 3


def test_empty_inputs_give_empty_typed_frames():
    for fn in (rates_to_df, ticks_to_df, deals_to_df):
        df = fn(None)
        assert df.empty and str(df.index.tz) == "UTC"


def test_ticks_millisecond_index_and_flags():
    arr = np.zeros(3, dtype=TICK_DTYPE)
    arr[0] = (T0, 1.10, 1.11, 0.0, 0, T0 * 1000 + 123, 2 | 4, 0)  # bid+ask update
    arr[1] = (T0, 1.10, 1.11, 1.105, 5, T0 * 1000 + 456, 8 | 32, 5)  # buy trade
    arr[2] = (T0, 1.10, 1.11, 1.104, 3, T0 * 1000 + 789, 8 | 64, 3)  # sell trade
    df = ticks_to_df(arr)
    assert df.index[0].microsecond == 123_000
    assert list(df["is_bid"]) == [True, False, False]
    assert list(df["is_buy"]) == [False, True, False] and list(df["is_sell"]) == [False, False, True]
    assert list(df["is_trade"]) == [False, True, True]


Deal = namedtuple("Deal", "ticket time time_msc type entry symbol volume price profit commission swap fee")


def test_deals_from_namedtuples_get_readable_names_and_net_profit():
    ms = T0 * 1000
    deals = [
        Deal(2, T0 + 60, ms + 60_000, 1, 1, "EURUSD", 0.1, 1.1, 12.0, -0.7, -0.3, 0.0),
        Deal(1, T0, ms, 0, 0, "EURUSD", 0.1, 1.09, 0.0, -0.7, 0.0, 0.0),
        Deal(3, T0 + 120, ms + 120_000, 2, 0, "", 0.0, 0.0, 500.0, 0.0, 0.0, 0.0),
    ]
    df = deals_to_df(deals)
    assert list(df["ticket"]) == [1, 2, 3]  # sorted by time
    assert list(df["type_name"]) == ["BUY", "SELL", "BALANCE"]
    assert list(df["entry_name"]) == ["IN", "OUT", "IN"]
    assert df.loc[df["ticket"] == 2, "net_profit"].iloc[0] == pytest.approx(11.0)


def test_deals_accept_plain_dicts_and_reject_garbage():
    df = deals_to_df([{"ticket": 1, "time": T0, "type": 99, "entry": 9, "profit": 1.0}])
    assert df["type_name"].iloc[0] == "UNKNOWN"
    with pytest.raises(TypeError):
        deals_to_df([object()])


def test_resample_ohlc_aggregates_correctly():
    df = rates_to_df(make_rates(6, 60))
    out = resample_ohlc(df, "3min")
    assert len(out) == 2
    first = out.iloc[0]
    assert first["open"] == 1.0 and first["close"] == 3.2 and first["high"] == 3.5 and first["low"] == 0.5
    assert first["tick_volume"] == 10 + 11 + 12 and first["spread"] == 4


def test_find_gaps_reports_missing_bars():
    arr = make_rates(5, 60)
    df = rates_to_df(np.delete(arr, [2, 3]))  # bars 2 and 3 missing
    gaps = find_gaps(df, "1min")
    assert len(gaps) == 1 and gaps["missing"].iloc[0] == 2
    assert find_gaps(rates_to_df(arr), "1min").empty


def test_parquet_roundtrip_keeps_utc_index(tmp_path):
    df = rates_to_df(make_rates())
    path = str(tmp_path / "bars.parquet")
    to_parquet(df, path)
    back = read_parquet(path)
    assert str(back.index.tz) == "UTC"
    pd.testing.assert_frame_equal(df, back, check_freq=False)


def test_as_float32_reduces_dtype_only_for_floats():
    out = as_float32(rates_to_df(make_rates()))
    assert out["open"].dtype == np.float32 and out["tick_volume"].dtype != np.float32
