"""
Engulfing Reversal — fade extremes using candlestick engulfing patterns.

Edge: A bullish/bearish engulfing candle at a swing extreme signals
institutional order flow reversing the move. Unlike RSI/BB mean reversion,
this uses PRICE ACTION confirmation (the engulfing body) rather than
indicator values. In ranging markets (low ADX), engulfing at recent
swing highs/lows produces high-probability reversals.

Uncorrelated with indicator-based strategies because it reads the
actual candle body dynamics, not derived oscillators.

Timeframe: H4 (best), H1 (viable)
Target: 3-8 trades/month, 50%+ WR with 1.5:1 RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, adx
from algosbz.strategy.base import Strategy


class EngulfingReversal(Strategy):

    DEFAULT_PARAMS = {
        "atr_period": 14,
        "adx_max": 30,               # Only in ranging/moderate markets
        "lookback": 20,              # Bars to find swing high/low
        "swing_zone_atr": 0.5,      # Price must be within 0.5 ATR of swing extreme
        "min_body_ratio": 0.6,       # Engulfing body must be >= 60% of total range
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.5,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Engulfing_Reversal"
        self._highs = None
        self._lows = None
        self._opens = None
        self._closes = None
        self._atr = None
        self._adx = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._opens = data["open"].values
        self._closes = data["close"].values
        self._atr = atr(data["high"], data["low"], data["close"], self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], data["close"], self.params["atr_period"]).values
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["atr_period"], p["lookback"]) + 5
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # ADX filter: skip strong trends
        if self._adx[idx] > p["adx_max"]:
            return no_action

        # Check for engulfing pattern at current bar
        curr_open = self._opens[idx]
        curr_close = self._closes[idx]
        curr_high = self._highs[idx]
        curr_low = self._lows[idx]

        prev_open = self._opens[idx - 1]
        prev_close = self._closes[idx - 1]

        curr_body = abs(curr_close - curr_open)
        curr_range = curr_high - curr_low
        if curr_range <= 0:
            return no_action

        # Body must be significant portion of the candle
        if curr_body / curr_range < p["min_body_ratio"]:
            return no_action

        # Find recent swing high and low
        lb = p["lookback"]
        recent_high = max(self._highs[idx - lb:idx])
        recent_low = min(self._lows[idx - lb:idx])

        price = curr_close
        zone = p["swing_zone_atr"] * current_atr

        # BULLISH ENGULFING: near swing low
        is_bullish_engulf = (
            curr_close > curr_open and          # Current bar is bullish
            prev_close < prev_open and           # Previous bar is bearish
            curr_open <= prev_close and           # Current open <= prev close
            curr_close >= prev_open               # Current close >= prev open (engulfs)
        )

        if is_bullish_engulf and curr_low <= recent_low + zone:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "pattern": "bullish_engulfing"},
            )

        # BEARISH ENGULFING: near swing high
        is_bearish_engulf = (
            curr_close < curr_open and          # Current bar is bearish
            prev_close > prev_open and           # Previous bar is bullish
            curr_open >= prev_close and           # Current open >= prev close
            curr_close <= prev_open               # Current close <= prev open (engulfs)
        )

        if is_bearish_engulf and curr_high >= recent_high - zone:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "pattern": "bearish_engulfing"},
            )

        return no_action
