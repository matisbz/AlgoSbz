"""
SMC Order Block — enter at institutional demand/supply zones.

Edge: Order blocks mark where large players accumulated positions.
When price returns to these zones after a structure break, it tends
to bounce. Combined with a rejection candle for confirmation, this
captures high-probability reversals with tight risk.

Timeframe: H1, H4
Target: 20-50 trades/year per instrument
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr
from algosbz.data.indicators_advanced import order_blocks, structure_breaks
from algosbz.strategy.base import Strategy


class SMCOrderBlock(Strategy):

    DEFAULT_PARAMS = {
        "swing_left": 5,
        "swing_right": 3,
        "atr_period": 14,
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,
        "rejection_wick_ratio": 0.5,  # Wick must be >= 50% of candle range
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "SMC_OrderBlock"
        self._atr = None
        self._ob_bull_top = None
        self._ob_bull_bottom = None
        self._ob_bear_top = None
        self._ob_bear_bottom = None
        self._breaks_bias = None
        self._opens = None
        self._highs = None
        self._lows = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        high = data["high"]
        low = data["low"]
        open_ = data["open"]

        self._atr = atr(high, low, close, self.params["atr_period"])

        ob = order_blocks(
            open_, high, low, close,
            self.params["swing_left"], self.params["swing_right"],
        )
        self._ob_bull_top = ob["ob_bull_top"].values
        self._ob_bull_bottom = ob["ob_bull_bottom"].values
        self._ob_bear_top = ob["ob_bear_top"].values
        self._ob_bear_bottom = ob["ob_bear_bottom"].values

        # Structure bias for directional filter
        breaks = structure_breaks(high, low, self.params["swing_left"], self.params["swing_right"])
        bias = np.zeros(len(data), dtype=int)
        current_bias = 0
        for i in range(len(data)):
            if breaks["bos_bull"].iloc[i] or breaks["choch_bull"].iloc[i]:
                current_bias = 1
            elif breaks["bos_bear"].iloc[i] or breaks["choch_bear"].iloc[i]:
                current_bias = -1
            bias[i] = current_bias
        self._breaks_bias = bias

        self._opens = open_.values
        self._highs = high.values
        self._lows = low.values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        if idx < 50:
            return no_action

        if has_position:
            return no_action

        hour = self._hours[idx]
        if hour < p["session_start"] or hour >= p["session_end"]:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        price = self._closes[idx]
        candle_range = self._highs[idx] - self._lows[idx]
        if candle_range <= 0:
            return no_action

        # Bullish OB: price at demand zone + bullish structure bias + rejection candle
        if not np.isnan(self._ob_bull_top[idx]) and self._breaks_bias[idx] >= 0:
            # Rejection: long lower wick (bullish rejection at support)
            lower_wick = min(self._opens[idx], self._closes[idx]) - self._lows[idx]
            if lower_wick / candle_range >= p["rejection_wick_ratio"]:
                sl = price - p["sl_atr_mult"] * current_atr
                tp = price + p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_LONG,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "ob_zone": "bull"},
                )

        # Bearish OB: price at supply zone + bearish structure bias + rejection candle
        if not np.isnan(self._ob_bear_top[idx]) and self._breaks_bias[idx] <= 0:
            # Rejection: long upper wick (bearish rejection at resistance)
            upper_wick = self._highs[idx] - max(self._opens[idx], self._closes[idx])
            if upper_wick / candle_range >= p["rejection_wick_ratio"]:
                sl = price + p["sl_atr_mult"] * current_atr
                tp = price - p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_SHORT,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "ob_zone": "bear"},
                )

        return no_action
