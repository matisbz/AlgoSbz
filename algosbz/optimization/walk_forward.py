"""
Walk-forward optimization with in-sample/out-of-sample validation.
"""
import itertools
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from algosbz.backtest.engine import BacktestEngine
from algosbz.backtest.results import BacktestResult
from algosbz.core.config import AppConfig, InstrumentConfig
from algosbz.optimization.objective import prop_firm_objective
from algosbz.risk.equity_manager import EquityManager

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardConfig:
    is_ratio: float = 0.7          # 70% in-sample
    n_splits: int = 5
    anchored: bool = False         # False = rolling, True = expanding
    min_trades_is: int = 20
    min_trades_oos: int = 5


@dataclass
class WFWindow:
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp
    best_params: dict
    is_score: float
    oos_result: Optional[BacktestResult]
    oos_score: float


@dataclass
class WalkForwardResult:
    windows: list[WFWindow]
    combined_oos_result: Optional[BacktestResult]
    avg_is_score: float
    avg_oos_score: float
    degradation: float  # (is_score - oos_score) / is_score — overfitting indicator

    @property
    def is_robust(self) -> bool:
        return self.degradation < 0.5 and self.avg_oos_score > 0


class WalkForwardOptimizer:

    def __init__(self, config: WalkForwardConfig = None):
        self.config = config or WalkForwardConfig()

    def run(
        self,
        strategy_class: type,
        data: pd.DataFrame,
        symbol: str,
        param_grid: dict,
        engine_config: AppConfig,
        instrument: InstrumentConfig,
    ) -> WalkForwardResult:
        windows = self._create_windows(data)
        wf_windows = []

        combos = self._param_combinations(param_grid)
        logger.info("Walk-forward: %d windows, %d param combos", len(windows), len(combos))

        for i, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            logger.info("Window %d/%d: IS [%s to %s] OOS [%s to %s]",
                        i + 1, len(windows), is_start.date(), is_end.date(),
                        oos_start.date(), oos_end.date())

            is_data = data[(data.index >= is_start) & (data.index < is_end)]
            oos_data = data[(data.index >= oos_start) & (data.index < oos_end)]

            if is_data.empty or oos_data.empty:
                continue

            # Optimize on IS
            best_score = -1
            best_params = {}
            for params in combos:
                strategy = strategy_class(params)
                engine = BacktestEngine(engine_config, instrument)
                result = engine.run(strategy, is_data, symbol)

                if result.total_trades < self.config.min_trades_is:
                    continue

                score = prop_firm_objective(result)
                if score > best_score:
                    best_score = score
                    best_params = params.copy()

            if not best_params:
                logger.warning("Window %d: no valid params found in IS", i + 1)
                continue

            # Run best params on OOS
            strategy = strategy_class(best_params)
            engine = BacktestEngine(engine_config, instrument)
            oos_result = engine.run(strategy, oos_data, symbol)
            oos_score = prop_firm_objective(oos_result) if oos_result.total_trades >= self.config.min_trades_oos else 0

            wf_windows.append(WFWindow(
                is_start=is_start, is_end=is_end,
                oos_start=oos_start, oos_end=oos_end,
                best_params=best_params,
                is_score=best_score,
                oos_result=oos_result,
                oos_score=oos_score,
            ))

            logger.info("  Best IS score: %.2f, OOS score: %.2f, OOS trades: %d",
                        best_score, oos_score, oos_result.total_trades)

        # Combine OOS results
        if not wf_windows:
            return WalkForwardResult([], None, 0, 0, 1.0)

        avg_is = np.mean([w.is_score for w in wf_windows])
        avg_oos = np.mean([w.oos_score for w in wf_windows])
        degradation = (avg_is - avg_oos) / avg_is if avg_is > 0 else 1.0

        # Combine OOS trades and equity
        all_oos_trades = []
        for w in wf_windows:
            if w.oos_result:
                all_oos_trades.extend(w.oos_result.trades)

        combined = None
        if all_oos_trades:
            # Build combined equity from OOS segments
            all_equity = pd.concat([
                w.oos_result.equity_curve for w in wf_windows
                if w.oos_result and not w.oos_result.equity_curve.empty
            ])
            if not all_equity.empty:
                combined = BacktestResult(all_oos_trades, all_equity, engine_config.account.initial_balance)

        return WalkForwardResult(
            windows=wf_windows,
            combined_oos_result=combined,
            avg_is_score=avg_is,
            avg_oos_score=avg_oos,
            degradation=degradation,
        )

    def _create_windows(self, data: pd.DataFrame):
        start = data.index[0]
        end = data.index[-1]
        total_days = (end - start).days

        window_days = total_days / self.config.n_splits
        is_days = int(window_days * self.config.is_ratio)
        oos_days = int(window_days * (1 - self.config.is_ratio))

        windows = []
        for i in range(self.config.n_splits):
            if self.config.anchored:
                is_start = start
            else:
                is_start = start + pd.Timedelta(days=int(i * window_days))

            is_end = is_start + pd.Timedelta(days=is_days)
            oos_start = is_end
            oos_end = oos_start + pd.Timedelta(days=oos_days)

            if oos_end > end:
                oos_end = end

            windows.append((is_start, is_end, oos_start, oos_end))

        return windows

    def _param_combinations(self, grid: dict) -> list[dict]:
        keys = list(grid.keys())
        values = list(grid.values())
        return [dict(zip(keys, combo)) for combo in itertools.product(*values)]

    def parameter_sensitivity(
        self,
        strategy_class: type,
        best_params: dict,
        data: pd.DataFrame,
        symbol: str,
        engine_config: AppConfig,
        instrument: InstrumentConfig,
        perturbation_pct: float = 0.2,
    ) -> dict:
        """Test robustness by perturbing each parameter."""
        base_strategy = strategy_class(best_params)
        engine = BacktestEngine(engine_config, instrument)
        base_result = engine.run(base_strategy, data, symbol)
        base_score = prop_firm_objective(base_result)

        sensitivity = {}
        for key, val in best_params.items():
            if not isinstance(val, (int, float)):
                continue

            scores = []
            for mult in [1 - perturbation_pct, 1 + perturbation_pct]:
                perturbed = best_params.copy()
                new_val = val * mult
                if isinstance(val, int):
                    new_val = max(1, int(round(new_val)))
                perturbed[key] = new_val

                strategy = strategy_class(perturbed)
                engine = BacktestEngine(engine_config, instrument)
                result = engine.run(strategy, data, symbol)
                scores.append(prop_firm_objective(result))

            avg_change = abs(np.mean(scores) - base_score) / max(base_score, 0.01)
            sensitivity[key] = round(avg_change, 3)

        return sensitivity
