"""
Multi-strategy portfolio engine.
Runs multiple strategies simultaneously with correlation-aware position management.
"""
import logging
from dataclasses import dataclass

import pandas as pd

from algosbz.backtest.engine import BacktestEngine
from algosbz.backtest.results import BacktestResult
from algosbz.core.config import AppConfig, InstrumentConfig
from algosbz.risk.equity_manager import EquityManager

logger = logging.getLogger(__name__)

# Static correlation map between pairs
CORRELATION_MAP = {
    ("EURUSD", "USDCHF"): -0.85,
    ("EURUSD", "GBPJPY"): 0.30,
    ("USDJPY", "GBPJPY"): 0.70,
    ("EURUSD", "USDJPY"): -0.40,
    ("XAUUSD", "EURUSD"): 0.30,
    ("XAUUSD", "USDJPY"): -0.50,
}


@dataclass
class StrategyAllocation:
    strategy_class: type
    strategy_params: dict
    symbols: list[str]
    weight: float = 1.0  # relative weight for capital allocation


class PortfolioEngine:

    def __init__(
        self,
        config: AppConfig,
        instruments: dict[str, InstrumentConfig],
        equity_manager: EquityManager = None,
    ):
        self.config = config
        self.instruments = instruments
        self.equity_manager = equity_manager
        self._allocations: list[StrategyAllocation] = []

    def add_strategy(self, allocation: StrategyAllocation):
        self._allocations.append(allocation)

    def run(self, data_dict: dict[str, pd.DataFrame]) -> dict:
        """
        Run all strategy-symbol combinations independently,
        then combine into a portfolio view.

        Returns dict with:
        - 'individual': {name: BacktestResult}
        - 'combined': BacktestResult (combined equity)
        - 'correlation_matrix': pd.DataFrame
        """
        if not self._allocations:
            raise ValueError("No strategies added to portfolio")

        total_weight = sum(a.weight for a in self._allocations)
        individual_results = {}

        for alloc in self._allocations:
            for symbol in alloc.symbols:
                if symbol not in data_dict:
                    logger.warning("No data for %s, skipping", symbol)
                    continue

                if symbol not in self.instruments:
                    logger.warning("No instrument config for %s, skipping", symbol)
                    continue

                strategy = alloc.strategy_class(alloc.strategy_params)
                name = f"{strategy.name}_{symbol}"

                # Adjust initial balance by weight
                weight_frac = alloc.weight / total_weight
                adjusted_config = self.config.model_copy(deep=True)
                adjusted_config.account.initial_balance *= weight_frac

                engine = BacktestEngine(
                    adjusted_config,
                    self.instruments[symbol],
                    equity_manager=self.equity_manager,
                )

                data = data_dict[symbol]
                result = engine.run(strategy, data, symbol)
                individual_results[name] = result

                logger.info(
                    "Portfolio: %s — %d trades, %.2f%% return",
                    name, result.total_trades, result.total_return_pct,
                )

        # Build combined equity curve
        combined_equity = self._combine_equity(individual_results)
        all_trades = []
        for result in individual_results.values():
            all_trades.extend(result.trades)
        all_trades.sort(key=lambda t: t.entry_time)

        combined_result = BacktestResult(
            trades=all_trades,
            equity_curve=combined_equity,
            initial_balance=self.config.account.initial_balance,
        )

        # Correlation matrix of daily returns
        corr_matrix = self._compute_correlations(individual_results)

        return {
            "individual": individual_results,
            "combined": combined_result,
            "correlation_matrix": corr_matrix,
        }

    def _combine_equity(self, results: dict[str, BacktestResult]) -> pd.Series:
        if not results:
            return pd.Series(dtype=float, name="equity")

        all_equity = {}
        for name, result in results.items():
            if not result.equity_curve.empty:
                all_equity[name] = result.equity_curve

        if not all_equity:
            return pd.Series(dtype=float, name="equity")

        df = pd.DataFrame(all_equity)
        df = df.ffill().bfill()

        # Sum all equity curves
        combined = df.sum(axis=1)
        combined.name = "equity"
        return combined

    def _compute_correlations(self, results: dict[str, BacktestResult]) -> pd.DataFrame:
        daily_returns = {}
        for name, result in results.items():
            if not result.equity_curve.empty:
                daily = result.equity_curve.resample("1D").last().dropna()
                if len(daily) > 1:
                    daily_returns[name] = daily.pct_change().dropna()

        if len(daily_returns) < 2:
            return pd.DataFrame()

        df = pd.DataFrame(daily_returns)
        return df.corr()

    @staticmethod
    def are_correlated(symbol1: str, symbol2: str, threshold: float = 0.6) -> bool:
        pair = tuple(sorted([symbol1, symbol2]))
        reverse = (pair[1], pair[0])

        corr = CORRELATION_MAP.get(pair, CORRELATION_MAP.get(reverse, 0.0))
        return abs(corr) >= threshold
