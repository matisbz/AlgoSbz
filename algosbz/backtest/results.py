from datetime import timedelta
from typing import Optional

import numpy as np
import pandas as pd

from algosbz.core.models import Trade


class BacktestResult:

    def __init__(
        self,
        trades: list[Trade],
        equity_curve: pd.Series,
        initial_balance: float,
    ):
        self.trades = trades
        self.equity_curve = equity_curve
        self.initial_balance = initial_balance

    @property
    def total_return_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        return (self.equity_curve.iloc[-1] - self.initial_balance) / self.initial_balance * 100

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades * 100

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @property
    def sharpe_ratio(self) -> float:
        if self.equity_curve.empty or len(self.equity_curve) < 2:
            return 0.0
        returns = self.equity_curve.pct_change().dropna()
        if returns.std() == 0:
            return 0.0
        # Annualized (assuming ~252 trading days, ~1440 bars/day for M1)
        daily_returns = returns.resample("1D").sum().dropna()
        if daily_returns.empty or daily_returns.std() == 0:
            return 0.0
        return (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

    @property
    def max_drawdown_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        peak = self.equity_curve.expanding().max()
        dd = (self.equity_curve - peak) / peak
        return abs(dd.min()) * 100

    @property
    def max_daily_drawdown_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        daily = self.equity_curve.resample("1D").last().dropna()
        if len(daily) < 2:
            return 0.0
        daily_returns = daily.pct_change().dropna()
        if daily_returns.empty:
            return 0.0
        return abs(daily_returns.min()) * 100

    @property
    def avg_risk_reward(self) -> float:
        if not self.trades:
            return 0.0
        rrs = [t.risk_reward for t in self.trades]
        return np.mean(rrs)

    @property
    def avg_trade_pnl(self) -> float:
        if not self.trades:
            return 0.0
        return np.mean([t.pnl for t in self.trades])

    @property
    def avg_trade_duration(self) -> Optional[timedelta]:
        if not self.trades:
            return None
        durations = [(t.exit_time - t.entry_time) for t in self.trades]
        avg_seconds = np.mean([d.total_seconds() for d in durations])
        return timedelta(seconds=avg_seconds)

    @property
    def trading_days(self) -> int:
        if not self.trades:
            return 0
        days = set()
        for t in self.trades:
            days.add(t.entry_time.date())
            days.add(t.exit_time.date())
        return len(days)

    @property
    def final_equity(self) -> float:
        if self.equity_curve.empty:
            return self.initial_balance
        return self.equity_curve.iloc[-1]

    def drawdown_series(self) -> pd.Series:
        if self.equity_curve.empty:
            return pd.Series(dtype=float)
        peak = self.equity_curve.expanding().max()
        return (self.equity_curve - peak) / peak * 100

    def to_trades_dataframe(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()

        records = []
        for t in self.trades:
            records.append({
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction.name,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "volume": t.volume,
                "pnl": round(t.pnl, 2),
                "pnl_pips": round(t.pnl_pips, 1),
                "risk_reward": round(t.risk_reward, 2),
                "exit_reason": t.exit_reason.value,
                "commission": round(t.commission, 2),
            })
        return pd.DataFrame(records)

    def metrics_summary(self) -> dict:
        return {
            "Total Trades": self.total_trades,
            "Win Rate (%)": round(self.win_rate, 1),
            "Profit Factor": round(self.profit_factor, 2),
            "Sharpe Ratio": round(self.sharpe_ratio, 2),
            "Max Drawdown (%)": round(self.max_drawdown_pct, 2),
            "Total Return (%)": round(self.total_return_pct, 2),
            "Final Equity": round(self.final_equity, 2),
            "Avg RR": round(self.avg_risk_reward, 2),
            "Avg Trade PnL": round(self.avg_trade_pnl, 2),
            "Trading Days": self.trading_days,
            "Winning Trades": self.winning_trades,
            "Losing Trades": self.losing_trades,
        }
