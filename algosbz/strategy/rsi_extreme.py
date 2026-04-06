"""
RSI Extreme Reversal — fade extreme RSI readings with candle confirmation.

Edge: RSI below 20 or above 80 indicates genuine exhaustion. Combined with
a confirming reversal candle (bullish close after RSI <20, bearish after >80),
this captures snap-back moves. Different from VMR which uses Bollinger Bands.

Timeframe: H1, H4
Target: 30-60 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import rsi, atr, adx
from algosbz.strategy.base import Strategy


class RSIExtreme(Strategy):

    DEFAULT_PARAMS = {
        "rsi_period": 14,
        "rsi_oversold": 20,
        "rsi_overbought": 80,
        "atr_period": 14,
        "adx_max": 35,             # avoid strongest trends
        "sl_atr_mult": 2.5,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "RSI_Extreme"
        self._rsi = None
        self._atr = None
        self._adx = None
        self._opens = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._rsi = rsi(close, self.params["rsi_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["atr_period"]).values
        self._opens = data["open"].values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["rsi_period"], p["atr_period"]) + 5
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
        if np.isnan(adx_val) or adx_val > p["adx_max"]:
            return no_action

        rsi_now = self._rsi[idx]
        rsi_prev = self._rsi[idx - 1]
        if np.isnan(rsi_now) or np.isnan(rsi_prev):
            return no_action

        price = self._closes[idx]

        # LONG: RSI was oversold on previous bar, current bar closes bullish (confirmation)
        if rsi_prev <= p["rsi_oversold"] and self._closes[idx] > self._opens[idx]:
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

        # SHORT: RSI was overbought on previous bar, current bar closes bearish
        if rsi_prev >= p["rsi_overbought"] and self._closes[idx] < self._opens[idx]:
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
