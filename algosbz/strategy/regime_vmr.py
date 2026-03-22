"""
Regime-Adaptive Volatility Mean Reversion.

Wraps the proven VMR strategy with a RegimeDetector filter.
Only trades when the market is RANGING with acceptable volatility
and during prime/acceptable sessions (kill zones).

Goal: Unlock VMR on instruments where raw VMR fails due to
trading during trending or extreme-volatility regimes.

Timeframe: H1, H4
"""
import pandas as pd

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.regime import RegimeDetector
from algosbz.strategy.base import Strategy
from algosbz.strategy.volatility_mean_reversion import VolatilityMeanReversion


class RegimeAdaptiveVMR(Strategy):

    DEFAULT_PARAMS = {
        # VMR params (passed through)
        "bb_period": 20,
        "bb_std": 2.5,
        "atr_period": 14,
        "consec_outside": 2,
        "adx_max": 30,
        "sl_atr_mult": 3.0,
        "tp_atr_mult": 4.0,
        "session_start": 0,
        "session_end": 23,
        "timeframe": "H1",
    }

    def __init__(self, params: dict = None):
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(merged)
        self.name = "Regime_VMR"
        self._vmr = VolatilityMeanReversion(merged)
        self._regime = RegimeDetector()
        self._symbol = ""

    def required_timeframe(self) -> str:
        return self.params["timeframe"]

    def setup(self, data: pd.DataFrame) -> None:
        self._vmr.setup(data)
        self._regime.compute(data)
        self._symbol = self._vmr._symbol

    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        no_action = Signal(action=SignalAction.NO_ACTION, symbol=self._symbol, timestamp=bar.name)

        if has_position:
            return no_action

        # Regime filter: only trade in ranging + prime/acceptable session
        if not self._regime.should_trade(idx, "mean_reversion"):
            return no_action

        return self._vmr.on_bar(idx, bar, has_position)
