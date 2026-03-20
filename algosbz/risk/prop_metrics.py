"""
Advanced prop firm metrics.
Pass-rate-focused Monte Carlo with block sampling to preserve trade autocorrelation.
"""
import numpy as np
import pandas as pd

from algosbz.backtest.results import BacktestResult
from algosbz.core.config import ChallengePhaseConfig


class PropFirmMetrics:

    @staticmethod
    def daily_pnl_distribution(result: BacktestResult) -> dict:
        if result.equity_curve.empty:
            return {}
        daily = result.equity_curve.resample("1D").last().dropna()
        returns = daily.pct_change().dropna()
        if returns.empty:
            return {}
        return {
            "mean": float(returns.mean() * 100),
            "std": float(returns.std() * 100),
            "skew": float(returns.skew()),
            "kurtosis": float(returns.kurtosis()),
            "p5": float(returns.quantile(0.05) * 100),
            "p25": float(returns.quantile(0.25) * 100),
            "p50": float(returns.quantile(0.50) * 100),
            "p75": float(returns.quantile(0.75) * 100),
            "p95": float(returns.quantile(0.95) * 100),
            "worst_day": float(returns.min() * 100),
            "best_day": float(returns.max() * 100),
            "pct_profitable_days": float((returns > 0).mean() * 100),
        }

    @staticmethod
    def calmar_ratio(result: BacktestResult) -> float:
        if result.equity_curve.empty or result.max_drawdown_pct == 0:
            return 0.0
        # Annualize return
        days = (result.equity_curve.index[-1] - result.equity_curve.index[0]).days
        if days <= 0:
            return 0.0
        annual_return = result.total_return_pct * (365 / days)
        return annual_return / result.max_drawdown_pct

    @staticmethod
    def estimated_pass_rate(
        result: BacktestResult,
        phase: ChallengePhaseConfig,
        n_simulations: int = 5000,
        window_trades: int = 50,
        block_size: int = 5,
    ) -> dict:
        """
        Enhanced Monte Carlo with block sampling.
        Samples consecutive blocks of trades to preserve autocorrelation.
        """
        if not result.trades or len(result.trades) < block_size:
            return {"pass_rate": 0.0, "n_simulations": 0}

        trade_pnls = [t.pnl for t in result.trades]
        n_total = len(trade_pnls)
        initial = result.initial_balance

        rng = np.random.default_rng(42)
        passes = 0
        fail_reasons = {"daily_dd": 0, "total_dd": 0, "profit": 0}
        max_dds = []
        profits = []

        n_blocks = max(1, window_trades // block_size)

        for _ in range(n_simulations):
            equity = initial
            peak = initial
            day_start = initial
            max_dd = 0.0
            max_daily_dd = 0.0
            trade_count = 0

            for _ in range(n_blocks):
                # Pick a random block start
                start = rng.integers(0, max(1, n_total - block_size))
                block = trade_pnls[start: start + block_size]

                for pnl in block:
                    equity += pnl
                    trade_count += 1

                    if equity > peak:
                        peak = equity

                    # Overall DD
                    dd = (peak - equity) / peak if peak > 0 else 0
                    max_dd = max(max_dd, dd)

                    # Simulate daily reset every ~8 trades (rough day proxy)
                    if trade_count % 8 == 0:
                        daily_dd = (day_start - equity) / day_start if day_start > 0 else 0
                        max_daily_dd = max(max_daily_dd, max(0, daily_dd))
                        day_start = equity

            profit_pct = (equity - initial) / initial
            profits.append(profit_pct)
            max_dds.append(max_dd)

            # Check pass
            profit_ok = profit_pct >= phase.profit_target
            dd_ok = max_dd < phase.max_dd_limit
            daily_ok = max_daily_dd < phase.daily_dd_limit

            if profit_ok and dd_ok and daily_ok:
                passes += 1
            else:
                if not dd_ok:
                    fail_reasons["total_dd"] += 1
                if not daily_ok:
                    fail_reasons["daily_dd"] += 1
                if not profit_ok:
                    fail_reasons["profit"] += 1

        profits_arr = np.array(profits)
        max_dds_arr = np.array(max_dds)

        return {
            "pass_rate": passes / n_simulations * 100,
            "n_simulations": n_simulations,
            "avg_profit": float(profits_arr.mean() * 100),
            "median_profit": float(np.median(profits_arr) * 100),
            "avg_max_dd": float(max_dds_arr.mean() * 100),
            "p5_profit": float(np.percentile(profits_arr, 5) * 100),
            "p95_profit": float(np.percentile(profits_arr, 95) * 100),
            "fail_breakdown": {
                k: v / n_simulations * 100 for k, v in fail_reasons.items()
            },
        }

    @staticmethod
    def robustness_score(result: BacktestResult, phase: ChallengePhaseConfig) -> float:
        """0-100 composite robustness score."""
        if not result.trades:
            return 0.0

        mc = PropFirmMetrics.estimated_pass_rate(result, phase, n_simulations=1000)
        pass_rate = mc["pass_rate"]

        # DD margin
        dd_margin = max(0, 1 - result.max_drawdown_pct / (phase.max_dd_limit * 100))

        # Daily profitability
        daily_dist = PropFirmMetrics.daily_pnl_distribution(result)
        pct_profitable = daily_dist.get("pct_profitable_days", 0) / 100

        # Win rate stability (use overall)
        wr = result.win_rate / 100

        # Calmar
        calmar = min(PropFirmMetrics.calmar_ratio(result) / 5, 1.0)

        score = (
            pass_rate * 0.30 +
            dd_margin * 100 * 0.25 +
            pct_profitable * 100 * 0.20 +
            wr * 100 * 0.15 +
            calmar * 100 * 0.10
        )

        return min(100, max(0, score))
