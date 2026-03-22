"""
Inside Bar Breakout — trade volatility expansion after compression.

Edge: An inside bar (high < prev high AND low > prev low) signals
market indecision and compression. The breakout from this range
tends to be directional, especially when it aligns with the trend
(ADX filter) or after multiple consecutive inside bars.

This is a PURE PRICE ACTION strategy — no oscillators or bands.
Uncorrelated with indicator-based mean reversion strategies.

Timeframe: H4 (best), H1 (viable)
Target: 2-5 trades/month, 45%+ WR with 2:1+ RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, ema
from algosbz.strategy.base import Strategy


class InsideBarBreakout(Strategy):

    DEFAULT_PARAMS = {
        "atr_period": 14,
        "trend_ema": 50,             # EMA for trend direction
        "min_inside_bars": 1,        # Minimum consecutive inside bars
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,          # 2:1 RR
        "min_bar_range_pct": 0.3,    # Inside bar must be at least 30% of mother bar
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Inside_Bar_Breakout"
        self._highs = None
        self._lows = None
        self._opens = None
        self._closes = None
        self._atr = None
        self._trend_ema = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._opens = data["open"].values
        self._closes = data["close"].values
        self._atr = atr(data["high"], data["low"], data["close"], self.params["atr_period"])
        self._trend_ema = ema(data["close"], self.params["trend_ema"]).values
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["atr_period"], p["trend_ema"]) + 5
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # Count consecutive inside bars ending at idx-1
        # (current bar idx is the potential breakout bar)
        inside_count = 0
        mother_high = self._highs[idx - 1]
        mother_low = self._lows[idx - 1]

        for j in range(idx - 1, max(idx - 6, min_bars - 1), -1):
            prev = j - 1
            if prev < 0:
                break
            if self._highs[j] < self._highs[prev] and self._lows[j] > self._lows[prev]:
                inside_count += 1
                # Update mother bar to the outermost containing bar
                mother_high = max(mother_high, self._highs[prev])
                mother_low = min(mother_low, self._lows[prev])
            else:
                break

        if inside_count < p["min_inside_bars"]:
            return no_action

        # Validate inside bar range isn't too tiny (avoid dojis in dead markets)
        inside_range = self._highs[idx - 1] - self._lows[idx - 1]
        mother_range = mother_high - mother_low
        if mother_range <= 0:
            return no_action
        if inside_range / mother_range < p["min_bar_range_pct"]:
            return no_action

        # Current bar breaks out of the mother bar range
        price = self._closes[idx]
        trend_up = price > self._trend_ema[idx]

        # LONG: close above mother high + trend is up
        if price > mother_high and trend_up:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "inside_bars": inside_count},
            )

        # SHORT: close below mother low + trend is down
        if price < mother_low and not trend_up:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "inside_bars": inside_count},
            )

        return no_action
