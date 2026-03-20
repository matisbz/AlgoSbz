"""
H4 Mean Reversion — fade extreme RSI readings on H4 in range-bound markets.

Edge: On H4, RSI extremes (<25 or >75) combined with price at Bollinger Band
extremes indicate multi-bar overextension. In low-ADX environments, these
extremes revert within 2-5 bars (8-20 hours), capturing 1-2 ATR of move.

Different from H1 VMR: H4 signals are more significant (less noise),
RSI adds confirmation beyond just BB position, and tighter TP captures
reversions faster (higher WR).

Timeframe: H4
Target: 8-15 trades/month, 55%+ WR with 1:1.5 RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, rsi, bollinger_bands, adx
from algosbz.strategy.base import Strategy


class H4MeanReversion(Strategy):

    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "atr_period": 14,
        "adx_max": 30,               # Only in ranging markets
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.0,          # 1.33 RR — tighter TP for faster captures
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "H4_Mean_Reversion"
        self._bb_upper = None
        self._bb_lower = None
        self._rsi = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        bb = bollinger_bands(close, self.params["bb_period"], self.params["bb_std"])
        self._bb_upper = bb["bb_upper"].values
        self._bb_lower = bb["bb_lower"].values
        self._rsi = rsi(close, self.params["rsi_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["atr_period"]).values
        self._closes = close.values
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["bb_period"], p["rsi_period"], p["atr_period"]) + 10
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0:
            return no_action

        # Range filter: skip trending markets
        if self._adx[idx] > p["adx_max"]:
            return no_action

        price = self._closes[idx]
        rsi_val = self._rsi[idx]
        if np.isnan(rsi_val):
            return no_action

        # LONG: RSI oversold + price below lower BB
        if rsi_val < p["rsi_oversold"] and price < self._bb_lower[idx]:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "rsi": rsi_val},
            )

        # SHORT: RSI overbought + price above upper BB
        if rsi_val > p["rsi_overbought"] and price > self._bb_upper[idx]:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "rsi": rsi_val},
            )

        return no_action
