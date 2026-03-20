"""
Market regime detector for strategy filtering.
Pre-computes regime data for the full dataset, provides O(1) per-bar lookups.
"""
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from algosbz.data.indicators_advanced import (
    atr_percentile,
    ema_ribbon_score,
    kill_zone_mask,
    structure_breaks,
    trend_strength_composite,
)


class TrendState(Enum):
    STRONG_BULL = "strong_bull"
    WEAK_BULL = "weak_bull"
    RANGING = "ranging"
    WEAK_BEAR = "weak_bear"
    STRONG_BEAR = "strong_bear"


class VolatilityState(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class SessionQuality(Enum):
    PRIME = "prime"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    NO_TRADE = "no_trade"


@dataclass
class RegimeSnapshot:
    trend: TrendState
    volatility: VolatilityState
    session_quality: SessionQuality
    trend_score: float
    atr_percentile: float
    is_kill_zone: bool
    structure_bias: str  # "bullish", "bearish", "neutral"


class RegimeDetector:

    def __init__(self, params: dict = None):
        self.params = params or {}
        self._trend_scores: np.ndarray = None
        self._atr_pctls: np.ndarray = None
        self._kz_mask: np.ndarray = None
        self._structure_bias: np.ndarray = None
        self._computed = False
        self._n = 0

    def compute(self, data: pd.DataFrame) -> None:
        """Pre-compute all regime indicators on the full dataset."""
        close = data["close"]
        high = data["high"]
        low = data["low"]

        # Trend strength
        ts = trend_strength_composite(close, high, low)
        self._trend_scores = ts.values

        # Volatility
        ap = atr_percentile(high, low, close)
        self._atr_pctls = ap.values

        # Kill zones
        kz = kill_zone_mask(data.index)
        self._kz_mask = kz.values

        # Structure bias (rolling)
        breaks = structure_breaks(high, low, swing_left=5, swing_right=3)
        bias = np.full(len(data), 0, dtype=int)  # 0=neutral, 1=bull, -1=bear
        current_bias = 0
        for i in range(len(data)):
            if breaks["bos_bull"].iloc[i] or breaks["choch_bull"].iloc[i]:
                current_bias = 1
            elif breaks["bos_bear"].iloc[i] or breaks["choch_bear"].iloc[i]:
                current_bias = -1
            bias[i] = current_bias
        self._structure_bias = bias

        self._n = len(data)
        self._computed = True

    def get_regime(self, idx: int) -> RegimeSnapshot:
        """O(1) lookup of regime at bar index."""
        if not self._computed or idx < 0 or idx >= self._n:
            return RegimeSnapshot(
                trend=TrendState.RANGING,
                volatility=VolatilityState.NORMAL,
                session_quality=SessionQuality.POOR,
                trend_score=0, atr_percentile=50,
                is_kill_zone=False, structure_bias="neutral",
            )

        ts = self._trend_scores[idx]
        ap = self._atr_pctls[idx]
        kz = bool(self._kz_mask[idx])
        sb = self._structure_bias[idx]

        # Classify trend
        if np.isnan(ts):
            trend = TrendState.RANGING
        elif ts > 40:
            trend = TrendState.STRONG_BULL
        elif ts > 15:
            trend = TrendState.WEAK_BULL
        elif ts < -40:
            trend = TrendState.STRONG_BEAR
        elif ts < -15:
            trend = TrendState.WEAK_BEAR
        else:
            trend = TrendState.RANGING

        # Classify volatility
        if np.isnan(ap):
            vol = VolatilityState.NORMAL
        elif ap < 25:
            vol = VolatilityState.LOW
        elif ap < 75:
            vol = VolatilityState.NORMAL
        elif ap < 95:
            vol = VolatilityState.HIGH
        else:
            vol = VolatilityState.EXTREME

        # Session quality
        if kz and vol in (VolatilityState.NORMAL, VolatilityState.HIGH):
            sq = SessionQuality.PRIME
        elif kz:
            sq = SessionQuality.ACCEPTABLE
        elif vol == VolatilityState.EXTREME:
            sq = SessionQuality.NO_TRADE
        else:
            sq = SessionQuality.POOR

        # Structure bias
        bias_str = "neutral"
        if sb == 1:
            bias_str = "bullish"
        elif sb == -1:
            bias_str = "bearish"

        return RegimeSnapshot(
            trend=trend,
            volatility=vol,
            session_quality=sq,
            trend_score=float(ts) if not np.isnan(ts) else 0.0,
            atr_percentile=float(ap) if not np.isnan(ap) else 50.0,
            is_kill_zone=kz,
            structure_bias=bias_str,
        )

    def should_trade(self, idx: int, strategy_type: str = "trend") -> bool:
        """High-level filter for different strategy types."""
        regime = self.get_regime(idx)

        # Never trade in extreme volatility or no-trade sessions
        if regime.volatility == VolatilityState.EXTREME:
            return False
        if regime.session_quality == SessionQuality.NO_TRADE:
            return False

        if strategy_type == "trend":
            return regime.trend in (
                TrendState.STRONG_BULL, TrendState.STRONG_BEAR,
                TrendState.WEAK_BULL, TrendState.WEAK_BEAR,
            ) and regime.session_quality in (SessionQuality.PRIME, SessionQuality.ACCEPTABLE)

        elif strategy_type == "mean_reversion":
            return regime.trend == TrendState.RANGING and regime.session_quality in (
                SessionQuality.PRIME, SessionQuality.ACCEPTABLE,
            )

        elif strategy_type == "breakout":
            return regime.volatility in (
                VolatilityState.LOW, VolatilityState.NORMAL,
            ) and regime.session_quality in (SessionQuality.PRIME, SessionQuality.ACCEPTABLE)

        return True
