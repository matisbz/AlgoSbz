"""
Keltner Squeeze Breakout — trade volatility expansion after contraction.

Edge: When Bollinger Bands contract inside Keltner Channels, the market is
in a "squeeze" (low volatility). When BB expand back outside KC, a breakout
follows in the momentum direction. This is different from IBB (inside bars)
because it measures statistical vol contraction, not just single-bar ranges.

Timeframe: H1, H4
Target: 30-60 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import ema, atr, bollinger_bands
from algosbz.strategy.base import Strategy


class KeltnerSqueeze(Strategy):

    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.0,
        "kc_period": 20,
        "kc_atr_mult": 1.5,        # KC width
        "atr_period": 14,
        "squeeze_bars": 3,          # min bars in squeeze before breakout
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 3.5,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Keltner_Squeeze"
        self._bb_upper = None
        self._bb_lower = None
        self._kc_upper = None
        self._kc_lower = None
        self._atr = None
        self._closes = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        high = data["high"]
        low = data["low"]

        # Bollinger Bands
        bb = bollinger_bands(close, self.params["bb_period"], self.params["bb_std"])
        self._bb_upper = bb["bb_upper"].values
        self._bb_lower = bb["bb_lower"].values

        # Keltner Channels: EMA ± ATR * mult
        kc_mid = ema(close, self.params["kc_period"])
        kc_atr = atr(high, low, close, self.params["kc_period"])
        self._kc_upper = (kc_mid + self.params["kc_atr_mult"] * kc_atr).values
        self._kc_lower = (kc_mid - self.params["kc_atr_mult"] * kc_atr).values

        self._atr = atr(high, low, close, self.params["atr_period"])
        self._closes = close.values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def _is_squeeze(self, idx: int) -> bool:
        """BB inside KC = squeeze (low volatility)."""
        return (self._bb_upper[idx] < self._kc_upper[idx] and
                self._bb_lower[idx] > self._kc_lower[idx])

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["bb_period"], p["kc_period"], p["atr_period"]) + p["squeeze_bars"] + 5
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

        # Check: was in squeeze for N bars, now NOT in squeeze (expansion)
        now_squeeze = self._is_squeeze(idx)
        if now_squeeze:
            return no_action  # still squeezed, no breakout yet

        # Verify prior bars were in squeeze
        prior_squeeze_count = 0
        for j in range(1, p["squeeze_bars"] + 5):
            k = idx - j
            if k < 0:
                break
            if self._is_squeeze(k):
                prior_squeeze_count += 1
            else:
                break

        if prior_squeeze_count < p["squeeze_bars"]:
            return no_action

        # Breakout direction: price vs midpoint of KC
        price = self._closes[idx]
        kc_mid = (self._kc_upper[idx] + self._kc_lower[idx]) / 2

        if price > kc_mid:
            # Bullish breakout
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
            # Bearish breakout
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
