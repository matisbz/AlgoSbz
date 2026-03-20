"""
Trend Pullback — enter during pullbacks in established trends.

Edge: Strong trends (EMA alignment + ADX) persist for many bars.
Pullbacks to the fast EMA zone offer low-risk entries with the trend.
The pullback ZONE is a multi-bar state, not a single-bar event,
so entering at next bar's open still catches the resumption.

Key: We enter when price IS in the pullback zone (near EMA),
NOT after it bounces. The bounce hasn't happened yet.

Timeframe: H1
Target: 100-200 trades/year
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr, ema, rsi, adx
from algosbz.strategy.base import Strategy


class TrendPullback(Strategy):

    DEFAULT_PARAMS = {
        "fast_ema": 21,
        "slow_ema": 50,
        "trend_ema": 200,
        "atr_period": 14,
        "adx_min": 25,            # need strong trend
        "pullback_zone_atr": 0.5, # price must be within 0.5 ATR of fast EMA
        "sl_atr_mult": 2.0,
        "tp_atr_mult": 3.0,      # 1.5 RR — wider stops for H1
        "session_start": 7,
        "session_end": 20,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Trend_Pullback"
        self._ema_fast = None
        self._ema_slow = None
        self._ema_trend = None
        self._atr = None
        self._adx = None
        self._closes = None
        self._lows = None
        self._highs = None
        self._hours = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        self._ema_fast = ema(close, self.params["fast_ema"]).values
        self._ema_slow = ema(close, self.params["slow_ema"]).values
        self._ema_trend = ema(close, self.params["trend_ema"]).values
        self._atr = atr(data["high"], data["low"], close, self.params["atr_period"])
        self._adx = adx(data["high"], data["low"], close, self.params["atr_period"]).values
        self._closes = close.values
        self._lows = data["low"].values
        self._highs = data["high"].values
        self._hours = data.index.hour
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        min_bars = p["trend_ema"] + 5
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

        adx_val = self._adx[idx]
        if adx_val < p["adx_min"]:
            return no_action

        ema_f = self._ema_fast[idx]
        ema_s = self._ema_slow[idx]
        ema_t = self._ema_trend[idx]
        price = self._closes[idx]

        pullback_zone = p["pullback_zone_atr"] * current_atr

        # UPTREND: EMAs aligned bullish
        if ema_f > ema_s > ema_t:
            # Price is in pullback zone: near or below fast EMA but above slow EMA
            # This means the pullback is HAPPENING (not finished)
            dist_to_ema = ema_f - price
            if 0 < dist_to_ema <= pullback_zone and price > ema_s:
                sl = ema_s - p["sl_atr_mult"] * current_atr
                tp = price + p["tp_atr_mult"] * current_atr
                return Signal(
                    action=SignalAction.ENTER_LONG,
                    symbol=self._symbol,
                    timestamp=bar.name,
                    stop_loss=sl,
                    take_profit=tp,
                    metadata={"ref_price": price},
                )

        # DOWNTREND: EMAs aligned bearish
        if ema_f < ema_s < ema_t:
            dist_to_ema = price - ema_f
            if 0 < dist_to_ema <= pullback_zone and price < ema_s:
                sl = ema_s + p["sl_atr_mult"] * current_atr
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
