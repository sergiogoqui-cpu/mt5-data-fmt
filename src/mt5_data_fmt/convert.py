"""Convert MetaTrader 5 API results into clean, UTC-indexed pandas DataFrames.

The ``MetaTrader5`` Python package returns numpy structured arrays and named tuples whose
timestamps are *broker server time encoded as if it were UTC*. These helpers fix that and give
you tidy frames. They only need numpy and pandas: MT5 itself is not imported, so everything is
testable on any OS.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

DEAL_TYPES = {
    0: "BUY",
    1: "SELL",
    2: "BALANCE",
    3: "CREDIT",
    4: "CHARGE",
    5: "CORRECTION",
    6: "BONUS",
    7: "COMMISSION",
    8: "COMMISSION_DAILY",
    9: "COMMISSION_MONTHLY",
    10: "COMMISSION_AGENT_DAILY",
    11: "COMMISSION_AGENT_MONTHLY",
    12: "INTEREST",
    13: "BUY_CANCELED",
    14: "SELL_CANCELED",
    15: "DIVIDEND",
    16: "DIVIDEND_FRANKED",
    17: "TAX",
}
DEAL_ENTRIES = {0: "IN", 1: "OUT", 2: "INOUT", 3: "OUT_BY"}

# MT5 tick flags
TICK_FLAG_BID, TICK_FLAG_ASK, TICK_FLAG_LAST = 2, 4, 8
TICK_FLAG_VOLUME, TICK_FLAG_BUY, TICK_FLAG_SELL = 16, 32, 64

OHLC_COLUMNS = ["open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def _to_utc(seconds: Any, server_utc_offset_hours: float, unit: str = "s") -> pd.DatetimeIndex:
    """MT5 stores server-local time as if it were UTC; subtract the offset to get real UTC."""
    idx = pd.to_datetime(seconds, unit=unit, utc=True)
    if server_utc_offset_hours:
        idx = idx - pd.Timedelta(hours=server_utc_offset_hours)
    # pandas 3 may infer seconds/milliseconds; pin nanoseconds so every pandas version (and a
    # Parquet round-trip) yields the same dtype.
    return pd.DatetimeIndex(idx).as_unit("ns")


def rates_to_df(rates: Any, server_utc_offset_hours: float = 0.0) -> pd.DataFrame:
    """OHLCV bars from ``copy_rates_*`` -> DataFrame indexed by UTC bar *open* time.

    ``server_utc_offset_hours`` is your broker's server offset from UTC (e.g. ``2`` or ``3``
    for many EET brokers; it changes with daylight saving, so pass the right one for the data).
    """
    if rates is None or len(rates) == 0:
        return pd.DataFrame(columns=OHLC_COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = pd.DataFrame(rates)
    df.index = _to_utc(df.pop("time"), server_utc_offset_hours)
    df.index.name = "time"
    cols = [c for c in OHLC_COLUMNS if c in df.columns]
    df = df[cols + [c for c in df.columns if c not in cols]]
    return df[~df.index.duplicated(keep="last")].sort_index()


def ticks_to_df(ticks: Any, server_utc_offset_hours: float = 0.0) -> pd.DataFrame:
    """Ticks from ``copy_ticks_*`` -> DataFrame with millisecond UTC index and decoded flags."""
    cols = ["bid", "ask", "last", "volume", "flags"]
    if ticks is None or len(ticks) == 0:
        return pd.DataFrame(columns=cols, index=pd.DatetimeIndex([], tz="UTC", name="time"))
    df = pd.DataFrame(ticks)
    stamp = df["time_msc"] if "time_msc" in df.columns else df["time"] * 1000
    df.index = _to_utc(stamp, server_utc_offset_hours, unit="ms")
    df.index.name = "time"
    df = df.drop(columns=[c for c in ("time", "time_msc") if c in df.columns])
    flags = df["flags"].astype(int)
    df["is_bid"] = (flags & TICK_FLAG_BID) > 0
    df["is_ask"] = (flags & TICK_FLAG_ASK) > 0
    df["is_trade"] = (flags & TICK_FLAG_LAST) > 0
    df["is_buy"] = (flags & TICK_FLAG_BUY) > 0
    df["is_sell"] = (flags & TICK_FLAG_SELL) > 0
    return df.sort_index()


def _deal_record(deal: Any) -> Mapping[str, Any]:
    if isinstance(deal, Mapping):
        return deal
    if hasattr(deal, "_asdict"):  # MT5 TradeDeal named tuple
        return deal._asdict()
    raise TypeError(f"unsupported deal object: {type(deal).__name__}")


def deals_to_df(deals: Iterable[Any] | None, server_utc_offset_hours: float = 0.0) -> pd.DataFrame:
    """Deals from ``history_deals_get`` -> DataFrame with names instead of numeric codes.

    Adds ``type_name`` (BUY/SELL/BALANCE...), ``entry_name`` (IN/OUT/...) and ``net_profit``
    (profit + commission + swap + fee).
    """
    rows = [dict(_deal_record(d)) for d in (deals or ())]
    if not rows:
        return pd.DataFrame(
            columns=["ticket", "symbol", "type_name", "entry_name", "volume", "price", "net_profit"],
            index=pd.DatetimeIndex([], tz="UTC", name="time"),
        )
    df = pd.DataFrame(rows)
    stamp = df["time_msc"] if "time_msc" in df.columns else df["time"] * 1000
    df.index = _to_utc(stamp, server_utc_offset_hours, unit="ms")
    df.index.name = "time"
    df = df.drop(columns=[c for c in ("time", "time_msc") if c in df.columns])
    df["type_name"] = df["type"].map(DEAL_TYPES).fillna("UNKNOWN")
    df["entry_name"] = df["entry"].map(DEAL_ENTRIES).fillna("UNKNOWN")
    money = [c for c in ("profit", "commission", "swap", "fee") if c in df.columns]
    df["net_profit"] = df[money].sum(axis=1)
    return df.sort_index()


_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "tick_volume": "sum",
    "real_volume": "sum",
    "spread": "max",
}


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate bars to a larger timeframe (``"5min"``, ``"1h"``, ``"1D"``...).

    Spread keeps the widest value in each window; empty windows are dropped.
    """
    agg = {k: v for k, v in _AGG.items() if k in df.columns}
    out = df.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open"])


def find_gaps(df: pd.DataFrame, freq: str, *, tolerance: int = 0) -> pd.DataFrame:
    """List holes in a regular bar series.

    Returns one row per gap with ``start`` (last bar before), ``end`` (next bar) and ``missing``
    (number of bars absent). Weekends and holidays show up as gaps: filter them to your calendar.
    """
    if len(df) < 2:
        return pd.DataFrame(columns=["start", "end", "missing"])
    step = pd.Timedelta(pd.tseries.frequencies.to_offset(freq))
    deltas = df.index.to_series().diff().iloc[1:]
    missing = (deltas / step).round().astype(int) - 1
    bad = missing > tolerance
    return pd.DataFrame(
        {
            "start": df.index[:-1][bad.to_numpy()],
            "end": df.index[1:][bad.to_numpy()],
            "missing": missing[bad].to_numpy(),
        }
    ).reset_index(drop=True)


def to_parquet(df: pd.DataFrame, path: str) -> None:
    """Save with the UTC index preserved (needs ``pyarrow``)."""
    df.to_parquet(path, engine="pyarrow", compression="zstd")


def read_parquet(path: str) -> pd.DataFrame:
    return pd.read_parquet(path, engine="pyarrow")


def as_float32(df: pd.DataFrame) -> pd.DataFrame:
    """Halve memory for large tick/bar frames by downcasting float64 columns."""
    out = df.copy()
    for col in out.select_dtypes(include=[np.float64]).columns:
        out[col] = out[col].astype(np.float32)
    return out
