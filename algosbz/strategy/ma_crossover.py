"""
MA Crossover — trend following via EMA crossover with ADX confirmation.

Edge: EMA fast/slow crossover is the simplest trend signal. Combined with
ADX filter (only trade in trending markets) and ATR-based SL/TP, it captures
sustained moves. Low correlation with mean-reversion strategies.

Timeframe: H1, H4
Target: 40-80 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import ema, atr, adx
from algosbz.strategy.base import Strategy


class MACrossover(Strategy):

    DEFAULT_PARAMS = {
        "fast_period": 8,
        "slow_period": 21,
        "atr_period": 14,
        "adx_min": 20,              # only trade in trending markets
        "adx_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 3.5,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "MA_Crossover"
        self._ema_fast = None
        self._ema_slow = None
        self._atr = None
        self._adx = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._ema_fast = ema(close, self.params["fast_period"]).values
        self._ema_slow = ema(close, self.params["slow_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["adx_period"]).values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["slow_period"], p["atr_period"], p["adx_period"]) + 5
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        hour = self._hours[idx]
        if hour < p["session_start"] or hour >= p["session_end"]:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0:
            return no_action

        # ADX filter: only trade when trending
        if self._adx[idx] < p["adx_min"]:
            return no_action

        # Check for crossover: fast crosses above slow (bullish)
        fast_now = self._ema_fast[idx]
        fast_prev = self._ema_fast[idx - 1]
        slow_now = self._ema_slow[idx]
        slow_prev = self._ema_slow[idx - 1]

        price = bar["close"]

        # Bullish crossover: fast was below slow, now above
        if fast_prev <= slow_prev and fast_now > slow_now:
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

        # Bearish crossover: fast was above slow, now below
        if fast_prev >= slow_prev and fast_now < slow_now:
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
