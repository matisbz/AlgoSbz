"""
Pin Bar Reversal — pure price action reversal at extreme levels.

Edge: A pin bar (hammer/shooting star) has a long wick and small body,
indicating rejection of a price level. When this occurs at recent swing
extremes (near Donchian high/low), it signals a potential reversal.

Key differences from Engulfing:
- Engulfing: 2-bar pattern, body-based, needs swing zone filter
- Pin Bar: 1-bar pattern, wick-based, needs level filter
These produce genuinely different signal timing and frequency.

No indicator dependency — pure price action + ATR for sizing.

Timeframe: H1, H4
Target: 30-60 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, donchian
from algosbz.strategy.base import Strategy


class PinBarReversal(Strategy):

    DEFAULT_PARAMS = {
        "min_wick_ratio": 2.0,      # wick must be 2x the body
        "max_body_pct": 0.35,       # body must be <35% of total range
        "level_lookback": 20,       # Donchian period for extreme level detection
        "level_proximity_atr": 0.5, # how close to Donchian extreme (in ATR)
        "atr_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Pin_Bar"
        self._atr = None
        self._dc_upper = None
        self._dc_lower = None
        self._opens = None
        self._closes = None
        self._highs = None
        self._lows = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        dc = donchian(data["high"], data["low"], self.params["level_lookback"])
        self._dc_upper = dc["dc_upper"].values
        self._dc_lower = dc["dc_lower"].values
        self._opens = data["open"].values
        self._closes = close.values
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["level_lookback"], p["atr_period"]) + 5
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

        o = self._opens[idx]
        c = self._closes[idx]
        h = self._highs[idx]
        l = self._lows[idx]

        total_range = h - l
        if total_range <= 0:
            return no_action

        body = abs(c - o)
        body_pct = body / total_range

        if body_pct > p["max_body_pct"]:
            return no_action

        # Upper wick and lower wick
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        price = c
        proximity = p["level_proximity_atr"] * current_atr

        # BULLISH pin bar: long lower wick, near Donchian low
        # Use previous bar's Donchian to avoid including current bar's low
        if lower_wick > body * p["min_wick_ratio"] and lower_wick > upper_wick * 1.5:
            dc_low = self._dc_lower[idx - 1]  # previous bar's Donchian low
            if not np.isnan(dc_low) and l <= dc_low + proximity:
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

        # BEARISH pin bar: long upper wick, near Donchian high
        if upper_wick > body * p["min_wick_ratio"] and upper_wick > lower_wick * 1.5:
            dc_high = self._dc_upper[idx - 1]
            if not np.isnan(dc_high) and h >= dc_high - proximity:
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
