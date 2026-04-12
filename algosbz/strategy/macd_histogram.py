"""
MACD Histogram Reversal — trade momentum deceleration.

Edge: When MACD histogram crosses zero from negative to positive (or vice versa),
it signals that momentum is shifting. This is different from MA crossover because:
- MACross triggers when fast EMA crosses slow EMA (= MACD crosses zero line)
- This triggers when the HISTOGRAM crosses zero (= MACD crosses its signal line)
The histogram zero-cross is a faster, earlier signal that captures momentum shifts
before the full EMA crossover completes.

Additional filter: require histogram to have been extreme (beyond a threshold)
before crossing zero, to avoid choppy signals.

Timeframe: H1, H4
Target: 40-80 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import macd, atr, adx
from algosbz.strategy.base import Strategy


class MACDHistogram(Strategy):

    DEFAULT_PARAMS = {
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "atr_period": 14,
        "adx_min": 15,              # some trend required (not a range strategy)
        "adx_period": 14,
        "hist_threshold_atr": 0.3,  # histogram must exceed this * ATR before reversing
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "MACD_Histogram"
        self._histogram = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        macd_df = macd(close, self.params["macd_fast"], self.params["macd_slow"],
                       self.params["macd_signal"])
        self._histogram = macd_df["histogram"].values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["adx_period"]).values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["macd_slow"], p["atr_period"], p["adx_period"]) + p["macd_signal"] + 5
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        hour = self._hours[idx]
        if hour < p["session_start"] or hour >= p["session_end"]:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # ADX filter
        adx_val = self._adx[idx]
        if np.isnan(adx_val) or adx_val < p["adx_min"]:
            return no_action

        hist_now = self._histogram[idx]
        hist_prev = self._histogram[idx - 1]
        if np.isnan(hist_now) or np.isnan(hist_prev):
            return no_action

        threshold = p["hist_threshold_atr"] * current_atr
        price = self._closes[idx]

        # LONG: histogram crosses from negative to positive
        # AND was below -threshold at some point in last 5 bars (genuine reversal)
        if hist_prev <= 0 and hist_now > 0:
            was_extreme = False
            for j in range(1, 6):
                k = idx - j
                if k >= 0 and self._histogram[k] < -threshold:
                    was_extreme = True
                    break
            if was_extreme:
                sl = price - p["sl_atr_mult"] * current_atr
                tp = price + p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_LONG,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price},
                )

        # SHORT: histogram crosses from positive to negative
        if hist_prev >= 0 and hist_now < 0:
            was_extreme = False
            for j in range(1, 6):
                k = idx - j
                if k >= 0 and self._histogram[k] > threshold:
                    was_extreme = True
                    break
            if was_extreme:
                sl = price + p["sl_atr_mult"] * current_atr
                tp = price - p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_SHORT,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price},
                )

        return no_action
