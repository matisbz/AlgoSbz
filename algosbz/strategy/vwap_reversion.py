"""
VWAP Reversion — fade deviations from session VWAP.

Edge: VWAP represents the fair value weighted by volume. When price
deviates significantly (> threshold ATR) from VWAP during active
sessions, institutional rebalancing pulls it back. Combined with
kill zone timing for high-probability setups.

Timeframe: M15, H1
Target: 15-40 trades/month per instrument
"""
import pandas as pd
import numpy as np

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.indicators import atr
from algosbz.data.indicators_advanced import vwap as compute_vwap, kill_zone_mask
from algosbz.strategy.base import Strategy


class VWAPReversion(Strategy):

    DEFAULT_PARAMS = {
        "atr_period": 14,
        "deviation_atr": 0.5,       # Min deviation from VWAP in ATR units
        "max_deviation_atr": 3.0,   # Max deviation (avoid chasing breakouts)
        "sl_atr_mult": 1.0,
        "tp_atr_mult": 1.5,
        "require_kill_zone": True,
        "timeframe": "M15",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "VWAP_Reversion"
        self._atr = None
        self._vwap = None
        self._kz_mask = None
        self._closes = None
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        close = data["close"]
        high = data["high"]
        low = data["low"]

        self._atr = atr(high, low, close, self.params["atr_period"])

        # VWAP needs volume — use tick_volume if available, else fallback
        if "tick_volume" in data.columns:
            vol = data["tick_volume"]
        elif "volume" in data.columns:
            vol = data["volume"]
        else:
            vol = pd.Series(np.ones(len(data)), index=data.index)

        # Replace zero volume with 1 to avoid division issues
        vol = vol.replace(0, 1)

        self._vwap = compute_vwap(high, low, close, vol, session_reset=True).values
        self._kz_mask = kill_zone_mask(data.index).values
        self._closes = close.values
        self._symbol = getattr(data, "_symbol", "UNKNOWN")

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        p = self.params
        if idx < p["atr_period"] + 10:
            return no_action

        if has_position:
            return no_action

        # Kill zone filter
        if p["require_kill_zone"] and not self._kz_mask[idx]:
            return no_action

        current_atr = self._atr.iloc[idx]
        if current_atr <= 0 or np.isnan(current_atr):
            return no_action

        vwap_val = self._vwap[idx]
        if np.isnan(vwap_val) or vwap_val <= 0:
            return no_action

        price = self._closes[idx]
        deviation = (price - vwap_val) / current_atr

        # LONG: price is significantly below VWAP → expect reversion up
        if deviation <= -p["deviation_atr"] and deviation >= -p["max_deviation_atr"]:
            sl = price - p["sl_atr_mult"] * current_atr
            tp = price + p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_LONG,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "vwap": vwap_val, "deviation_atr": deviation},
            )

        # SHORT: price is significantly above VWAP → expect reversion down
        if deviation >= p["deviation_atr"] and deviation <= p["max_deviation_atr"]:
            sl = price + p["sl_atr_mult"] * current_atr
            tp = price - p["tp_atr_mult"] * current_atr
            return Signal(
                action=SignalAction.ENTER_SHORT,
                symbol=self._symbol,
                timestamp=bar.name,
                stop_loss=sl,
                take_profit=tp,
                metadata={"ref_price": price, "vwap": vwap_val, "deviation_atr": deviation},
            )

        return no_action
