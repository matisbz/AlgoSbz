"""
ADX Trend Birth — enter when a new trend is born from low-ADX conditions.

Edge: When ADX rises above a threshold from a low level, it signals the
birth of a new trend (direction doesn't matter for ADX, only strength).
Combined with EMA direction to determine long vs short.

Key differences from MACross:
- MACross triggers on EMA crossover (direction change)
- ADX Trend Birth triggers on ADX rising (strength increase from low)
- This catches trends that START without an EMA crossover (e.g., gradual
  acceleration in the existing direction)
- Different timing: fires when volatility expands, not when direction changes

Timeframe: H1, H4
Target: 20-50 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import ema, atr, adx
from algosbz.strategy.base import Strategy


class ADXTrendBirth(Strategy):

    DEFAULT_PARAMS = {
        "adx_period": 14,
        "adx_low": 20,              # must have been below this recently
        "adx_trigger": 25,          # ADX must cross above this
        "lookback_low": 8,          # bars to look back for low ADX
        "ema_period": 50,           # direction filter
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
        self.name = "ADX_Trend_Birth"
        self._adx = None
        self._ema = None
        self._atr = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._adx = adx(data["high"], data["low"], close, self.params["adx_period"]).values
        self._ema = ema(close, self.params["ema_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["adx_period"], p["ema_period"], p["atr_period"]) + p["lookback_low"] + 5
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

        adx_now = self._adx[idx]
        adx_prev = self._adx[idx - 1]
        if np.isnan(adx_now) or np.isnan(adx_prev):
            return no_action

        # ADX must cross above trigger on this bar
        if not (adx_prev < p["adx_trigger"] and adx_now >= p["adx_trigger"]):
            return no_action

        # ADX must have been below adx_low within lookback
        was_low = False
        for j in range(1, p["lookback_low"] + 1):
            k = idx - j
            if k >= 0 and not np.isnan(self._adx[k]) and self._adx[k] < p["adx_low"]:
                was_low = True
                break

        if not was_low:
            return no_action

        # Direction: use EMA
        price = self._closes[idx]
        ema_val = self._ema[idx]
        if np.isnan(ema_val):
            return no_action

        if price > ema_val:
            # Bullish trend birth
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
        else:
            # Bearish trend birth
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
