"""mt5-data-fmt: tidy, UTC-correct pandas DataFrames from MetaTrader 5 data."""

from .convert import (
    DEAL_ENTRIES,
    DEAL_TYPES,
    as_float32,
    deals_to_df,
    find_gaps,
    rates_to_df,
    read_parquet,
    resample_ohlc,
    ticks_to_df,
    to_parquet,
)

__all__ = [
    "DEAL_ENTRIES",
    "DEAL_TYPES",
    "as_float32",
    "deals_to_df",
    "find_gaps",
    "rates_to_df",
    "read_parquet",
    "resample_ohlc",
    "ticks_to_df",
    "to_parquet",
]
__version__ = "0.1.0"
