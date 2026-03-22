"""
FVG Reversion — mean reversion toward Fair Value Gaps.

Edge: Fair Value Gaps (imbalances) act as magnets — price tends to
fill them. When price returns to an unfilled FVG in a non-trending
market, it has high probability of reversing from there.

Combined with trend_strength_composite to avoid trading FVGs in
strong trends where price blows through them.

Timeframe: H1, H4
Target: 20-60 trades/year per instrument
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr
from algosbz.data.indicators_advanced import fair_value_gaps, trend_strength_composite
from algosbz.strategy.base import Strategy


class FVGReversion(Strategy):

    DEFAULT_PARAMS = {
        "atr_period": 14,
        "min_gap_atr_ratio": 0.3,
        "trend_strength_max": 25,   # Only trade when trend is weak (ranging)
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.5,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "FVG_Reversion"
        self._atr = None
        self._fvg_bull_top = None
        self._fvg_bull_bottom = None
        self._fvg_bear_top = None
        self._fvg_bear_bottom = None
        self._trend_strength = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        high = data["high"]
        low = data["low"]

        self._atr = atr(high, low, close, self.params["atr_period"])

        fvg = fair_value_gaps(
            high, low,
            min_gap_atr_ratio=self.params["min_gap_atr_ratio"],
            atr_period=self.params["atr_period"],
            close=close,
        )
        self._fvg_bull_top = fvg["fvg_bull_top"].values
        self._fvg_bull_bottom = fvg["fvg_bull_bottom"].values
        self._fvg_bear_top = fvg["fvg_bear_top"].values
        self._fvg_bear_bottom = fvg["fvg_bear_bottom"].values

        self._trend_strength = trend_strength_composite(close, high, low).values
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        if idx < 50:
            return no_action

        if has_position:
            return no_action

        hour = self._hours[idx]
        if hour < p["session_start"] or hour >= p["session_end"]:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # Trend filter: only trade in weak/ranging markets
        ts = self._trend_strength[idx]
        if np.isnan(ts) or abs(ts) > p["trend_strength_max"]:
            return no_action

        price = self._closes[idx]

        # Bullish FVG: price at a bullish gap → expect bounce UP
        if not np.isnan(self._fvg_bull_top[idx]):
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "fvg_type": "bull"},
            )

        # Bearish FVG: price at a bearish gap → expect bounce DOWN
        if not np.isnan(self._fvg_bear_top[idx]):
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "fvg_type": "bear"},
            )

        return no_action
