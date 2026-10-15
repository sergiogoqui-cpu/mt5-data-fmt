"""Works without MetaTrader 5 installed:  python examples/synthetic.py"""

import numpy as np

from mt5_data_fmt import find_gaps, rates_to_df, resample_ohlc

dtype = [("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
         ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")]
t0 = 1_772_445_600  # an arbitrary UTC second
rates = np.zeros(8, dtype=dtype)
for i in range(8):
    rates[i] = (t0 + i * 300, 1.10 + i / 1000, 1.101 + i / 1000, 1.099 + i / 1000, 1.1005 + i / 1000, 100 + i, 12, 0)
rates = np.delete(rates, [3, 4])  # simulate two missing M5 bars

bars = rates_to_df(rates, server_utc_offset_hours=3)
print(bars.head(3), end="\n\n")
print("gaps:\n", find_gaps(bars, "5min"), end="\n\n")
print(resample_ohlc(bars, "15min"))