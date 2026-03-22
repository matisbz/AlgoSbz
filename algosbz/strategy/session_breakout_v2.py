"""
Session Breakout — capture London/NY open volatility expansion.

Edge: Before major session opens, price consolidates in a tight range.
The first breakout during the kill zone (London 07-10, NY 12-15 UTC)
tends to continue as institutional order flow drives direction.

High-frequency strategy: M15 timeframe, 10-20 trades/month.
This is key for generating enough trades for exam independence.

Timeframe: M15
Target: 10-20 trades/month per instrument
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr
from algosbz.data.indicators_advanced import kill_zone_mask
from algosbz.strategy.base import Strategy


class SessionBreakout(Strategy):

    DEFAULT_PARAMS = {
        "pre_range_bars": 16,       # M15 bars before kill zone = 4 hours
        "atr_period": 14,
        "sl_atr_mult": 1.0,
        "tp_atr_mult": 2.0,
        "min_range_atr": 0.3,      # Min range relative to ATR (avoid too tight)
        "max_range_atr": 2.5,      # Max range relative to ATR (avoid trending)
        "timeframe": "M15",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Session_Breakout"
        self._atr = None
        self._highs = None
        self._lows = None
        self._closes = None
        self._kz_mask = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._closes = close.values
        self._kz_mask = kill_zone_mask(data.index).values
        self._hours = data.index.hour
        self._dates = data.index.date
        self._symbol = getattr(data, "_symbol", "UNKNOWN")
        self._n = len(data)

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = p["pre_range_bars"] + p["atr_period"] + 10
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        # Only trade during kill zones
        if not self._kz_mask[idx]:
            return no_action

        # Must be first bar entering kill zone (previous bar was NOT in kill zone)
        # OR early in the kill zone (allow first 4 bars = first hour)
        if idx > 0 and self._kz_mask[idx - 1]:
            # Already in kill zone — check if still early (within first 4 bars)
            bars_in_kz = 0
            for j in range(idx, max(idx - 8, 0) - 1, -1):
                if self._kz_mask[j]:
                    bars_in_kz += 1
                else:
                    break
            if bars_in_kz > 4:
                return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # Calculate pre-session range from bars before kill zone
        range_bars = p["pre_range_bars"]
        # Find the start of pre-range: go back to before kill zone
        pre_start = idx - 1
        while pre_start > 0 and self._kz_mask[pre_start]:
            pre_start -= 1

        pre_end = pre_start
        pre_start = max(0, pre_end - range_bars + 1)

        if pre_end - pre_start < 4:  # Need at least 4 bars for a range
            return no_action

        range_high = max(self._highs[pre_start:pre_end + 1])
        range_low = min(self._lows[pre_start:pre_end + 1])
        range_size = range_high - range_low

        # Filter: range must be within bounds relative to ATR
        if range_size < p["min_range_atr"] * current_atr:
            return no_action
        if range_size > p["max_range_atr"] * current_atr:
            return no_action

        price = self._closes[idx]

        # LONG: close above range high
        if price > range_high:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "range_high": range_high, "range_low": range_low},
            )

        # SHORT: close below range low
        if price < range_low:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "range_high": range_high, "range_low": range_low},
            )

        return no_action
