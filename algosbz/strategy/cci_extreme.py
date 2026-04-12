"""
CCI Extreme Reversal — fade extreme CCI readings with confirmation.

Edge: CCI (Commodity Channel Index) measures deviation of typical price
((H+L+C)/3) from its SMA, normalized by mean absolute deviation. At ±200
it signals statistically extreme readings. Different from:
- RSI: uses close-to-close momentum
- Bollinger Bands: uses close price standard deviation
- Stochastic: uses position within H/L range

CCI uses a different price construct (typical price) and a different
normalization (mean absolute deviation vs std dev), producing genuinely
different entry timing.

Timeframe: H1, H4
Target: 30-60 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import cci, atr, adx
from algosbz.strategy.base import Strategy


class CCIExtreme(Strategy):

    DEFAULT_PARAMS = {
        "cci_period": 20,
        "cci_extreme": 200,         # ±200 for extreme reading
        "atr_period": 14,
        "adx_max": 40,              # avoid extreme trends
        "adx_period": 14,
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H4",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "CCI_Extreme"
        self._cci = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._opens = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._cci = cci(data["high"], data["low"], close, self.params["cci_period"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["adx_period"]).values
        self._closes = close.values
        self._opens = data["open"].values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = max(p["cci_period"], p["atr_period"], p["adx_period"]) + 5
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

        adx_val = self._adx[idx]
        if np.isnan(adx_val) or adx_val > p["adx_max"]:
            return no_action

        cci_now = self._cci[idx]
        cci_prev = self._cci[idx - 1]
        if np.isnan(cci_now) or np.isnan(cci_prev):
            return no_action

        price = self._closes[idx]

        # LONG: CCI was below -extreme on prev bar, now recovering upward
        # + bullish candle confirmation (close > open)
        if cci_prev <= -p["cci_extreme"] and cci_now > cci_prev and self._closes[idx] > self._opens[idx]:
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

        # SHORT: CCI was above +extreme on prev bar, now declining
        # + bearish candle confirmation
        if cci_prev >= p["cci_extreme"] and cci_now < cci_prev and self._closes[idx] < self._opens[idx]:
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
