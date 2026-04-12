"""
Donchian Trend — pure channel breakout trend following (Turtle-style).

Edge: When price breaks the N-period high (or low), it signals the start
of a potentially large move. This is the simplest trend-following approach,
proven since the original Turtle Traders.

Key differences from SwBrk:
- SwBrk requires a prior squeeze (low volatility contraction) before breakout
- Donchian Trend fires on ANY new channel breakout, no squeeze required
- SwBrk has ADX filter; this uses Donchian width filter instead
- Different timing: SwBrk catches compression→expansion, this catches any new extreme

Additional filter: require that the channel is not too wide (volatility cap)
to avoid late entries in already-extended moves.

Timeframe: H1, H4
Target: 30-60 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import donchian, atr
from algosbz.strategy.base import Strategy


class DonchianTrend(Strategy):

    DEFAULT_PARAMS = {
        "entry_period": 20,          # breakout period (Turtle: 20)
        "max_channel_atr": 5.0,      # max channel width in ATR (avoid extended)
        "min_channel_atr": 1.5,      # min channel width (avoid noise)
        "atr_period": 14,
        "sl_atr_mult": 2.5,
        "tp_atr_mult": 5.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Donchian_Trend"
        self._dc_upper = None
        self._dc_lower = None
        self._atr = None
        self._closes = None
        self._highs = None
        self._lows = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        dc = donchian(data["high"], data["low"], self.params["entry_period"])
        self._dc_upper = dc["dc_upper"].values
        self._dc_lower = dc["dc_lower"].values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._closes = close.values
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["entry_period"], p["atr_period"]) + 5
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

        # Channel width filter: use PREVIOUS bar's Donchian to avoid look-ahead
        prev_upper = self._dc_upper[idx - 1]
        prev_lower = self._dc_lower[idx - 1]
        if np.isnan(prev_upper) or np.isnan(prev_lower):
            return no_action

        channel_width = prev_upper - prev_lower
        channel_atr_ratio = channel_width / current_atr

        if channel_atr_ratio > p["max_channel_atr"]:
            return no_action  # channel too wide, move already extended
        if channel_atr_ratio < p["min_channel_atr"]:
            return no_action  # channel too narrow, noise

        price = self._closes[idx]

        # LONG: close breaks above previous bar's Donchian upper
        if price > prev_upper:
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

        # SHORT: close breaks below previous bar's Donchian lower
        if price < prev_lower:
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
