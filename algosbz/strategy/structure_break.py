"""
Market Structure Break — trade changes in trend structure.

Edge: Markets trend through sequences of higher highs/higher lows (uptrend)
or lower highs/lower lows (downtrend). When this structure BREAKS
(e.g., a series of HH/HL suddenly makes a lower low), it signals
a potential trend reversal. Early entry on structure breaks captures
the beginning of new trends before lagging indicators confirm.

This is pure STRUCTURE analysis — no indicators, no oscillators.
Fundamentally different from EMA crossovers or momentum strategies.

Logic:
1. Detect swing points (local highs/lows using N-bar lookback)
2. Classify trend structure (HH/HL = up, LH/LL = down)
3. Enter when structure breaks (up→down or down→up)
4. ADX filter to avoid choppy/trendless markets

Timeframe: H1 (best), H4 (viable)
Target: 3-8 trades/month, 45%+ WR with 2:1 RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr
from algosbz.strategy.base import Strategy


class StructureBreak(Strategy):

    DEFAULT_PARAMS = {
        "swing_lookback": 5,         # Bars each side to confirm swing point
        "atr_period": 14,
        "min_swing_distance_atr": 0.5,  # Min distance between swings
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,         # 2:1 RR
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Structure_Break"
        self._highs = None
        self._lows = None
        self._closes = None
        self._atr = None
        self._swing_highs = None  # list of (idx, price)
        self._swing_lows = None   # list of (idx, price)
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._closes = data["close"].values
        self._atr = atr(data["high"], data["low"], data["close"], self.params["atr_period"])
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

        # Pre-compute swing points
        lb = self.params["swing_lookback"]
        n = len(data)
        self._swing_highs = []
        self._swing_lows = []

        for i in range(lb, n - lb):
            # Swing high: highest in the window
            window_high = max(self._highs[i - lb:i + lb + 1])
            if self._highs[i] == window_high:
                self._swing_highs.append((i, self._highs[i]))

            # Swing low: lowest in the window
            window_low = min(self._lows[i - lb:i + lb + 1])
            if self._lows[i] == window_low:
                self._swing_lows.append((i, self._lows[i]))

    def _get_recent_swings(self, idx: int, swing_list: list, count: int = 3):
        """Get the N most recent confirmed swing points before idx."""
        lb = self.params["swing_lookback"]
        # Only use swings confirmed by lb bars after them
        confirmed = [(i, p) for i, p in swing_list if i + lb <= idx]
        return confirmed[-count:] if len(confirmed) >= count else []

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = p["atr_period"] + p["swing_lookback"] * 3 + 20
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        min_dist = p["min_swing_distance_atr"] * current_atr

        # Get last 3 swing highs and lows
        recent_sh = self._get_recent_swings(idx, self._swing_highs, 3)
        recent_sl = self._get_recent_swings(idx, self._swing_lows, 3)

        if len(recent_sh) < 3 or len(recent_sl) < 3:
            return no_action

        price = self._closes[idx]

        # Detect bearish structure break:
        # Was making higher lows (uptrend), now breaks below the last swing low
        sl1, sl2, sl3 = [p for _, p in recent_sl]
        was_uptrend_lows = (sl2 > sl1 + min_dist) and (sl3 > sl2 - min_dist * 0.5)
        # Price breaks below the most recent swing low
        breaks_below = price < sl3 - min_dist * 0.3

        if was_uptrend_lows and breaks_below:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "break": "bearish", "broken_level": sl3},
            )

        # Detect bullish structure break:
        # Was making lower highs (downtrend), now breaks above the last swing high
        sh1, sh2, sh3 = [p for _, p in recent_sh]
        was_downtrend_highs = (sh2 < sh1 - min_dist) and (sh3 < sh2 + min_dist * 0.5)
        # Price breaks above the most recent swing high
        breaks_above = price > sh3 + min_dist * 0.3

        if was_downtrend_highs and breaks_above:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "break": "bullish", "broken_level": sh3},
            )

        return no_action
