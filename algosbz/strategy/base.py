from abc import ABC, abstractmethod

import pandas as pd

from algosbz.core.models import Signal


class Strategy(ABC):

    def __init__(self, params: dict = None):
        self.params = params or {}
        self.name: str = self.__class__.__name__

    @abstractmethod
    def setup(self, data: pd.DataFrame) -> None:
        """Pre-compute indicators on the full dataset. Called once before backtest loop."""
        ...

    @abstractmethod
    def on_bar(self, idx: int, bar: pd.Series, has_position: bool) -> Signal:
        """Called on every bar. Return a Signal with action, SL, TP."""
        ...

    @abstractmethod
    def required_timeframe(self) -> str:
        """Primary timeframe: 'M1','M5','M15','H1','H4','D1'"""
        ...
