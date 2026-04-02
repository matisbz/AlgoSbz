"""
Anti-martingale equity curve management.

Reduces position size during drawdowns, scales up during winning streaks,
and halts trading entirely when daily loss reaches safety threshold.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class EquityManagerConfig:
    # Drawdown tiers: (dd_pct_threshold, risk_multiplier)
    # DD measured from INITIAL BALANCE (static, like prop firms), not from peak
    dd_tiers: list[tuple[float, float]] = field(default_factory=lambda: [
        (0.03, 1.0),   # DD 0-3%: full risk
        (0.05, 0.5),   # DD 3-5%: half risk
        (0.07, 0.25),  # DD 5-7%: quarter risk
        (0.08, 0.0),   # DD >8%: stop (1% safety margin before 9% limit)
    ])
    consecutive_win_bonus: float = 0.1     # bonus per consecutive win
    max_multiplier: float = 1.3            # cap on upside scaling
    progressive_trades: int = 3            # ramp-up period (reduced from 5)
    daily_stop_threshold: float = 0.035    # hard stop at 3.5% daily DD
    streak_reset_on_loss: bool = True


class EquityManager:

    def __init__(self, config: EquityManagerConfig = None):
        self.config = config or EquityManagerConfig()
        self._initial_balance: float = 0.0
        self._current_equity: float = 0.0
        self._peak_equity: float = 0.0
        self._start_of_day_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._consecutive_wins: int = 0
        self._total_trades_in_window: int = 0
        self._last_date: Optional[date] = None
        self._daily_halted: bool = False

    def initialize(self, initial_balance: float):
        self._initial_balance = initial_balance
        self._current_equity = initial_balance
        self._peak_equity = initial_balance
        self._start_of_day_equity = initial_balance
        self._daily_pnl = 0.0
        self._consecutive_wins = 0
        self._total_trades_in_window = 0
        self._daily_halted = False

    def on_bar(self, timestamp: datetime):
        current_date = timestamp.date()
        if self._last_date is None:
            self._last_date = current_date

        # Daily reset
        if current_date > self._last_date:
            self._start_of_day_equity = self._current_equity
            self._daily_pnl = 0.0
            self._daily_halted = False
            self._last_date = current_date

    def on_trade_closed(self, pnl: float, equity: float):
        self._current_equity = equity
        self._daily_pnl += pnl
        self._total_trades_in_window += 1

        if equity > self._peak_equity:
            self._peak_equity = equity

        if pnl > 0:
            self._consecutive_wins += 1
        else:
            if self.config.streak_reset_on_loss:
                self._consecutive_wins = 0

        # Check daily stop — use initial_balance as denominator (matches RiskManager
        # and FTMO rules: daily DD = (start_of_day - equity) / initial_balance)
        if self._initial_balance > 0:
            daily_dd = (self._start_of_day_equity - equity) / self._initial_balance
            if daily_dd >= self.config.daily_stop_threshold:
                self._daily_halted = True

    def get_risk_multiplier(self) -> float:
        if self._daily_halted:
            return 0.0

        # Overall drawdown from INITIAL BALANCE (static, like prop firms)
        # This prevents death spirals where peak grows then DD% locks trading
        if self._initial_balance > 0:
            dd_from_initial = max(0, (self._initial_balance - self._current_equity) / self._initial_balance)
        else:
            dd_from_initial = 0.0

        # DD-based tier multiplier
        tier_mult = 0.0
        for threshold, mult in self.config.dd_tiers:
            if dd_from_initial < threshold:
                tier_mult = mult
                break

        if tier_mult <= 0:
            return 0.0

        # Progressive ramp-up for new windows
        progressive_mult = 1.0
        if self._total_trades_in_window < self.config.progressive_trades:
            n = self._total_trades_in_window
            total = self.config.progressive_trades
            # 0.5 -> 0.625 -> 0.75 -> 0.875 -> 1.0
            progressive_mult = 0.5 + 0.5 * (n / total)

        # Consecutive win bonus
        win_bonus = 1.0
        if self._consecutive_wins >= 3:
            win_bonus = 1.0 + self.config.consecutive_win_bonus * (self._consecutive_wins - 2)
            win_bonus = min(win_bonus, self.config.max_multiplier)

        final = tier_mult * progressive_mult * win_bonus
        return min(final, self.config.max_multiplier)

    def should_stop_trading(self) -> bool:
        return self._daily_halted

    @property
    def current_dd_pct(self) -> float:
        if self._initial_balance <= 0:
            return 0.0
        return max(0, (self._initial_balance - self._current_equity) / self._initial_balance)

    @property
    def daily_dd_pct(self) -> float:
        if self._initial_balance <= 0:
            return 0.0
        return max(0, (self._start_of_day_equity - self._current_equity) / self._initial_balance)
