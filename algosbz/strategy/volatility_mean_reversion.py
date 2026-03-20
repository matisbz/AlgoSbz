"""
Volatility Mean Reversion — fade sustained moves outside Bollinger Bands.

Edge: When price closes outside BB(20, 2.5) for 2+ consecutive bars,
the move is statistically overextended. The "outside BB" state persists
across bars, so entering at next bar's open still captures the snap-back.

Key: We require CONSECUTIVE bars outside the band — not just one touch.
This filters noise and ensures genuine overextension.

Timeframe: H1
Target: 60-120 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, bollinger_bands, adx
from algosbz.strategy.base import Strategy


class VolatilityMeanReversion(Strategy):

    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.5,             # wider bands = more extreme
        "atr_period": 14,
        "consec_outside": 2,       # bars outside BB required
        "adx_max": 30,             # avoid strong trends (mean reversion fails)
        "sl_atr_mult": 3.0,       # wide SL to avoid noise exits
        "tp_atr_mult": 4.0,       # 1.33 RR
        "session_start": 7,
        "session_end": 20,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Vol_Mean_Reversion"
        self._bb_upper = None
        self._bb_lower = None
        self._bb_middle = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        bb = bollinger_bands(close, self.params["bb_period"], self.params["bb_std"])
        self._bb_upper = bb["bb_upper"].values
        self._bb_lower = bb["bb_lower"].values
        self._bb_middle = bb["bb_middle"].values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["atr_period"]).values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["bb_period"], p["atr_period"]) + p["consec_outside"] + 5
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

        # ADX filter: skip strong trends where mean reversion fails
        adx_val = self._adx[idx]
        if adx_val > p["adx_max"]:
            return no_action

        price = self._closes[idx]
        n = p["consec_outside"]

        # Check consecutive closes BELOW lower BB → LONG
        all_below = True
        for j in range(n):
            k = idx - j
            if self._closes[k] >= self._bb_lower[k]:
                all_below = False
                break

        if all_below:
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

        # Check consecutive closes ABOVE upper BB → SHORT
        all_above = True
        for j in range(n):
            k = idx - j
            if self._closes[k] <= self._bb_upper[k]:
                all_above = False
                break

        if all_above:
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
