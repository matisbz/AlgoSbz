"""
Swing Breakout — capture multi-day directional moves on H4.

Edge: After a contraction period (low ATR relative to recent history),
volatility expands directionally. The first strong H4 close beyond a
Donchian channel during expansion tends to continue for 2-5 days.
This is driven by stop-loss cascades and momentum algorithms.

Optimized for FTMO challenges: fewer trades, bigger moves.
Target pairs: XTIUSD, XAUUSD, GBPJPY (high monthly range).

Logic:
1. Detect contraction: current ATR < squeeze_pct × ATR moving average
2. Wait for expansion: ATR crosses back above threshold
3. Enter on Donchian channel breakout (close beyond N-bar high/low)
4. Trail stop using ATR to ride the move
5. Max 1 position at a time

Timeframe: H4
Target: 5-15 trades/month, 2:1+ RR
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, ema, adx
from algosbz.strategy.base import Strategy


class SwingBreakout(Strategy):

    DEFAULT_PARAMS = {
        "donchian_period": 20,        # Bars for channel (20 H4 bars = ~3.3 days)
        "atr_period": 14,
        "atr_ma_period": 50,          # Moving average of ATR (for squeeze detection)
        "squeeze_pct": 0.8,           # ATR < 80% of ATR MA = squeeze
        "adx_min": 20,                # Need some trend strength
        "sl_atr_mult": 1.5,           # Tight initial SL
        "tp_atr_mult": 3.0,           # Wide TP for swing moves (2:1 RR)
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Swing_Breakout"
        self._atr = None
        self._atr_ma = None
        self._adx = None
        self._highs = None
        self._lows = None
        self._closes = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._atr_ma = self._atr.rolling(window=self.params["atr_ma_period"]).mean()
        self._adx = adx(data["high"], data["low"], close, self.params["atr_period"]).values
        self._highs = data["high"].values
        self._lows = data["low"].values
        self._closes = close.values
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["donchian_period"], p["atr_ma_period"]) + 10
        if idx < min_bars:
            return no_action

        if has_position:
            return no_action

        current_atr = self._atr.iloc[idx]
        atr_ma = self._atr_ma.iloc[idx]
        if current_atr <= 0 or pd.isna(atr_ma) or atr_ma <= 0:
            return no_action

        # ADX filter
        if self._adx[idx] < p["adx_min"]:
            return no_action

        # Squeeze detection: was ATR recently below threshold?
        # Check if ATR was in squeeze within the last 5 bars
        was_squeezed = False
        for j in range(1, 6):
            k = idx - j
            if k < 0:
                break
            past_atr = self._atr.iloc[k]
            past_ma = self._atr_ma.iloc[k]
            if pd.notna(past_ma) and past_ma > 0 and past_atr < p["squeeze_pct"] * past_ma:
                was_squeezed = True
                break

        if not was_squeezed:
            return no_action

        # Current ATR should be expanding (above squeeze level)
        if current_atr < p["squeeze_pct"] * atr_ma:
            return no_action  # Still in squeeze

        # Donchian channel breakout
        dc_period = p["donchian_period"]
        dc_high = max(self._highs[idx - dc_period:idx])  # Excludes current bar
        dc_low = min(self._lows[idx - dc_period:idx])

        price = self._closes[idx]

        # LONG: close above Donchian high
        if price > dc_high:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "dc_high": dc_high},
            )

        # SHORT: close below Donchian low
        if price < dc_low:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "dc_low": dc_low},
            )

        return no_action
