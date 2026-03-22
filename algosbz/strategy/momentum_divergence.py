"""
Momentum Divergence — trade RSI divergences with price.

Edge: When price makes a new high but RSI makes a LOWER high (bearish
divergence), momentum is fading and a reversal is likely. The opposite
for bullish divergence. This captures a fundamentally different edge
than direct RSI levels (overbought/oversold) — it reads MOMENTUM LOSS,
not absolute levels.

Unlike H4MR which enters on RSI extremes, this strategy enters when
RSI DISAGREES with price — a structural momentum signal.

Timeframe: H4 (best), H1 (viable)
Target: 2-6 trades/month, 48%+ WR with 1.5:1 RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, rsi
from algosbz.strategy.base import Strategy


class MomentumDivergence(Strategy):

    DEFAULT_PARAMS = {
        "rsi_period": 14,
        "atr_period": 14,
        "swing_lookback": 5,          # Bars each side for swing detection
        "divergence_window": 30,      # Max bars between two swing points
        "min_rsi_diff": 3,            # Min RSI difference for divergence
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 2.5,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Momentum_Divergence"
        self._highs = None
        self._lows = None
        self._closes = None
        self._rsi = None
        self._atr = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._closes = data["close"].values
        self._rsi = rsi(data["close"], self.params["rsi_period"]).values
        self._atr = atr(data["high"], data["low"], data["close"], self.params["atr_period"])
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def _find_swing_highs(self, idx: int, window: int, lb: int):
        """Find swing highs in the lookback window."""
        swings = []
        start = max(lb, idx - window)
        for i in range(start, idx - lb + 1):
            is_swing = True
            for j in range(1, lb + 1):
                if i - j < 0 or i + j >= len(self._highs):
                    is_swing = False
                    break
                if self._highs[i] < self._highs[i - j] or self._highs[i] < self._highs[i + j]:
                    is_swing = False
                    break
            if is_swing:
                swings.append((i, self._highs[i], self._rsi[i]))
        return swings

    def _find_swing_lows(self, idx: int, window: int, lb: int):
        """Find swing lows in the lookback window."""
        swings = []
        start = max(lb, idx - window)
        for i in range(start, idx - lb + 1):
            is_swing = True
            for j in range(1, lb + 1):
                if i - j < 0 or i + j >= len(self._lows):
                    is_swing = False
                    break
                if self._lows[i] > self._lows[i - j] or self._lows[i] > self._lows[i + j]:
                    is_swing = False
                    break
            if is_swing:
                swings.append((i, self._lows[i], self._rsi[i]))
        return swings

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = p["divergence_window"] + p["swing_lookback"] + p["rsi_period"] + 10
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        rsi_val = self._rsi[idx]
        if np.isnan(rsi_val):
            return no_action

        price = self._closes[idx]
        lb = p["swing_lookback"]
        window = p["divergence_window"]
        min_rsi_diff = p["min_rsi_diff"]

        # Check for BEARISH divergence: price higher high, RSI lower high
        swing_highs = self._find_swing_highs(idx, window, lb)
        if len(swing_highs) >= 2:
            prev_sh = swing_highs[-2]
            last_sh = swing_highs[-1]
            # Price: higher high
            # RSI: lower high (divergence)
            if (last_sh[1] > prev_sh[1] and
                last_sh[2] < prev_sh[2] - min_rsi_diff and
                idx - last_sh[0] <= lb + 2):  # Signal near the last swing
                sl = price + p["sl_atr_mult"] * current_atr
                tp = price - p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_SHORT,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "divergence": "bearish",
                              "rsi": rsi_val},
                )

        # Check for BULLISH divergence: price lower low, RSI higher low
        swing_lows = self._find_swing_lows(idx, window, lb)
        if len(swing_lows) >= 2:
            prev_sl = swing_lows[-2]
            last_sl = swing_lows[-1]
            # Price: lower low
            # RSI: higher low (divergence)
            if (last_sl[1] < prev_sl[1] and
                last_sl[2] > prev_sl[2] + min_rsi_diff and
                idx - last_sl[0] <= lb + 2):  # Signal near the last swing
                sl = price - p["sl_atr_mult"] * current_atr
                tp = price + p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_LONG,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price, "divergence": "bullish",
                              "rsi": rsi_val},
                )

        return no_action
