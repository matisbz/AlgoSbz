"""
Optimization objective functions targeting prop firm pass rate, not Sharpe.
"""
from algosbz.backtest.results import BacktestResult
from algosbz.core.config import ChallengePhaseConfig


def prop_firm_objective(result: BacktestResult, phase: ChallengePhaseConfig = None) -> float:
    """
    Objective that maximizes estimated pass rate.

    Score = (profit_factor * win_rate * avg_rr) / (max_dd^2)

    Penalties for:
    - max_dd > 8%: score *= 0.1
    - win_rate < 40%: score *= 0.5
    - total_trades < 10: score = 0
    """
    if result.total_trades < 10:
        return 0.0

    pf = result.profit_factor
    wr = result.win_rate / 100  # 0-1
    rr = max(0, result.avg_risk_reward)
    max_dd = result.max_drawdown_pct  # percentage

    if max_dd <= 0:
        max_dd = 0.01  # avoid division by zero

    score = (pf * wr * max(rr, 0.1)) / (max_dd ** 2) * 1000

    # Penalties
    if max_dd > 8:
        score *= 0.1
    elif max_dd > 6:
        score *= 0.5

    if wr < 0.40:
        score *= 0.5

    if pf < 1.0:
        score *= 0.3

    # Bonus for low drawdown
    if max_dd < 5:
        score *= 1.5

    return max(0, score)
