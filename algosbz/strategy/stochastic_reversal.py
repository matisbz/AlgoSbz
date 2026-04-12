"""
Stochastic Reversal — fade overbought/oversold with %K/%D crossover.

Edge: Stochastic oscillator measures where price is within its recent range
(high-low channel), which is fundamentally different from RSI (momentum of
price changes) and Bollinger Bands (standard deviation). When %K crosses %D
in extreme zones, it signals exhaustion of the current move.

Key differences from RSIext:
- RSI: momentum-based (rate of change of closes)
- Stochastic: range-based (position within recent high/low range)
- These produce genuinely different signals and timing

Timeframe: H1, H4
Target: 40-80 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import stochastic, atr, adx
from algosbz.strategy.base import Strategy


class StochasticReversal(Strategy):

    DEFAULT_PARAMS = {
        "k_period": 14,
        "d_period": 3,
        "oversold": 20,
        "overbought": 80,
        "atr_period": 14,
        "adx_max": 35,             # avoid strongest trends
        "adx_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Stochastic_Reversal"
        self._stoch_k = None
        self._stoch_d = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        stoch = stochastic(data["high"], data["low"], close,
                           self.params["k_period"], self.params["d_period"])
        self._stoch_k = stoch["stoch_k"].values
        self._stoch_d = stoch["stoch_d"].values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["adx_period"]).values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["k_period"], p["atr_period"], p["adx_period"]) + p["d_period"] + 5
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

        # ADX filter: avoid strong trends
        adx_val = self._adx[idx]
        if np.isnan(adx_val) or adx_val > p["adx_max"]:
            return no_action

        k_now = self._stoch_k[idx]
        k_prev = self._stoch_k[idx - 1]
        d_now = self._stoch_d[idx]
        d_prev = self._stoch_d[idx - 1]

        if np.isnan(k_now) or np.isnan(d_now) or np.isnan(k_prev) or np.isnan(d_prev):
            return no_action

        price = self._closes[idx]

        # LONG: %K crosses above %D in oversold zone
        if d_now < p["oversold"] and k_prev <= d_prev and k_now > d_now:
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

        # SHORT: %K crosses below %D in overbought zone
        if d_now > p["overbought"] and k_prev >= d_prev and k_now < d_now:
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
