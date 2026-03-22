"""
Keltner Squeeze Breakout — trade volatility expansion after BB squeezes inside Keltner.

Edge: When Bollinger Bands contract inside Keltner Channels, the market
is in extreme compression. When BBs expand back outside KC, a directional
move is imminent. This is the "TTM Squeeze" concept.

Different from SwingBreakout: SwBrk uses Donchian + ATR squeeze.
This uses BB/KC relationship — a more precise compression signal.
The momentum direction at squeeze release determines trade direction.

Logic:
1. Detect squeeze: BB upper < KC upper AND BB lower > KC lower
2. Wait for release: BBs expand back outside KC
3. Enter in momentum direction (close > open = long, close < open = short)
4. ADX rising filter to confirm expansion has momentum

Timeframe: H1 (best), H4 (viable)
Target: 3-8 trades/month, 45%+ WR with 2:1 RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, ema, sma, bollinger_bands
from algosbz.strategy.base import Strategy


class KeltnerSqueeze(Strategy):

    DEFAULT_PARAMS = {
        "bb_period": 20,
        "bb_std": 2.0,
        "kc_period": 20,
        "kc_mult": 1.5,              # Keltner = EMA ± mult × ATR
        "atr_period": 14,
        "squeeze_bars": 3,            # Min bars in squeeze before release
        "momentum_period": 12,        # Bars for momentum direction
        "sl_atr_mult": 1.5,
        "tp_atr_mult": 3.0,          # 2:1 RR
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Keltner_Squeeze"
        self._closes = None
        self._opens = None
        self._bb_upper = None
        self._bb_lower = None
        self._kc_upper = None
        self._kc_lower = None
        self._atr = None
        self._momentum = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        p = self.params

        # Bollinger Bands
        bb = bollinger_bands(close, p["bb_period"], p["bb_std"])
        self._bb_upper = bb["bb_upper"].values
        self._bb_lower = bb["bb_lower"].values

        # Keltner Channels
        kc_mid = ema(close, p["kc_period"])
        atr_vals = atr(data["high"], data["low"], close, p["atr_period"])
        self._kc_upper = (kc_mid + p["kc_mult"] * atr_vals).values
        self._kc_lower = (kc_mid - p["kc_mult"] * atr_vals).values

        self._atr = atr_vals
        self._closes = close.values
        self._opens = data["open"].values

        # Momentum: rate of change over N bars
        self._momentum = close.diff(p["momentum_period"]).values

        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["bb_period"], p["kc_period"], p["momentum_period"]) + p["squeeze_bars"] + 10
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        # Check if currently NOT in squeeze (release)
        in_squeeze_now = (self._bb_upper[idx] < self._kc_upper[idx] and
                         self._bb_lower[idx] > self._kc_lower[idx])

        if in_squeeze_now:
            return no_action  # Still squeezed

        # Check if was in squeeze in recent bars
        squeeze_count = 0
        for j in range(1, p["squeeze_bars"] + 5):
            k = idx - j
            if k < 0:
                break
            was_squeezed = (self._bb_upper[k] < self._kc_upper[k] and
                          self._bb_lower[k] > self._kc_lower[k])
            if was_squeezed:
                squeeze_count += 1
            elif squeeze_count > 0:
                break  # End of squeeze period

        if squeeze_count < p["squeeze_bars"]:
            return no_action  # Not enough squeeze before release

        # Just released from squeeze — determine direction from momentum
        momentum = self._momentum[idx]
        if np.isnan(momentum):
            return no_action

        price = self._closes[idx]

        # LONG: positive momentum at squeeze release
        if momentum > 0 and price > self._opens[idx]:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "squeeze_bars": squeeze_count,
                          "momentum": momentum},
            )

        # SHORT: negative momentum at squeeze release
        if momentum < 0 and price < self._opens[idx]:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "squeeze_bars": squeeze_count,
                          "momentum": momentum},
            )

        return no_action
