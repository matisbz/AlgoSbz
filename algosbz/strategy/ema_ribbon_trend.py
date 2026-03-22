"""
EMA Ribbon Trend Following — ride strong trends with pullback entries.

Edge: When all 5 EMAs (8, 13, 21, 34, 55) are perfectly aligned for
multiple bars, the trend is institutional-grade. Entering on RSI pullbacks
during these confirmed trends captures continuation moves with tight risk.

Timeframe: H1, H4
Target: 30-80 trades/year per instrument
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, rsi
from algosbz.data.indicators_advanced import ema_ribbon_score
from algosbz.strategy.base import Strategy


class EMARibbonTrend(Strategy):

    DEFAULT_PARAMS = {
        "ribbon_threshold": 0.7,
        "ribbon_confirm_bars": 3,
        "rsi_period": 14,
        "rsi_pullback_bull": 45,
        "rsi_pullback_bear": 55,
        "atr_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "EMA_Ribbon_Trend"
        self._ribbon = None
        self._rsi = None
        self._atr = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._ribbon = ema_ribbon_score(close).values
        self._rsi = rsi(close, self.params["rsi_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = 55 + p["ribbon_confirm_bars"] + 5
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

        current_rsi = self._rsi[idx]
        if np.isnan(current_rsi):
            return no_action

        ribbon = self._ribbon[idx]
        price = self._closes[idx]
        threshold = p["ribbon_threshold"]
        confirm = p["ribbon_confirm_bars"]

        # Check bullish trend: ribbon > threshold for N consecutive bars
        if ribbon >= threshold:
            all_confirmed = True
            for j in range(1, confirm + 1):
                if self._ribbon[idx - j] < threshold:
                    all_confirmed = False
                    break

            if all_confirmed and current_rsi <= p["rsi_pullback_bull"]:
                sl = price - p["sl_atr_mult"] * current_atr
                tp = price + p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_LONG,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "ribbon": ribbon, "rsi": current_rsi},
                )

        # Check bearish trend: ribbon < -threshold for N consecutive bars
        if ribbon <= -threshold:
            all_confirmed = True
            for j in range(1, confirm + 1):
                if self._ribbon[idx - j] > -threshold:
                    all_confirmed = False
                    break

            if all_confirmed and current_rsi >= p["rsi_pullback_bear"]:
                sl = price + p["sl_atr_mult"] * current_atr
                tp = price - p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_SHORT,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "ribbon": ribbon, "rsi": current_rsi},
                )

        return no_action
