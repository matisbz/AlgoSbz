"""
Challenge Risk Engine — dynamic position sizing for prop firm challenges.

The fundamental insight: a challenge is an OPTION, not an investment.
You pay $500 for the chance to get funded. The optimal strategy is
aggressive early (you only lose the fee), protective when ahead,
and calculated when behind.

This replaces the static EquityManager during challenge attempts.

Risk zones:
┌─────────────────────────────────────────────────────────┐
│  AHEAD OF PACE + NEAR TARGET  → COAST (1.0-1.5%)       │
│  AHEAD OF PACE                → PROTECT (1.5-2.0%)     │
│  ON PACE                      → STANDARD (2.5-3.0%)    │
│  BEHIND PACE (recoverable)    → AGGRESSIVE (3.0-4.0%)  │
│  NEAR DD LIMIT (>7%)          → SURVIVAL (0.5-1.0%)    │
│  DD BLOWN (>8.5%)             → HALT (0%)              │
└─────────────────────────────────────────────────────────┘
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class ChallengeRiskConfig:
    # Challenge targets
    profit_target: float = 0.08       # 8% for Phase 1
    max_calendar_days: int = 30       # Days to achieve target
    daily_dd_limit: float = 0.05      # 5% daily DD limit
    max_dd_limit: float = 0.10        # 10% total DD limit

    # Risk zones (base risk multipliers applied to base_risk_per_trade)
    base_risk: float = 0.02           # Base risk per trade

    # Aggressive zone: first days / behind pace
    aggressive_risk_mult: float = 1.75   # → 3.5% at base 2%
    # Standard zone: on pace
    standard_risk_mult: float = 1.25     # → 2.5%
    # Protect zone: ahead of pace
    protect_risk_mult: float = 0.875     # → 1.75%
    # Coast zone: near target (>75% of target reached)
    coast_risk_mult: float = 0.5         # → 1.0%
    # Survival zone: near DD limits
    survival_risk_mult: float = 0.375    # → 0.75%

    # Thresholds
    near_target_pct: float = 0.75     # 75% of target = coast mode
    dd_danger_zone: float = 0.065     # 6.5% DD = survival mode
    dd_halt: float = 0.085            # 8.5% DD = halt (safety margin)
    daily_dd_danger: float = 0.035    # 3.5% daily DD = reduce risk
    daily_dd_halt: float = 0.045      # 4.5% daily DD = halt for day

    # Ramp-up: first N trades are slightly reduced
    ramp_up_trades: int = 2
    ramp_up_mult: float = 0.75        # First trades at 75% of zone risk


class ChallengeEquityManager:
    """
    Dynamic risk manager optimized for prop firm challenge attempts.

    Instead of static risk, adjusts position size based on:
    1. Progress toward target (pace)
    2. Proximity to DD limits (survival)
    3. Time remaining (urgency)
    4. Recent performance (momentum)
    """

    def __init__(self, config: ChallengeRiskConfig = None):
        self.config = config or ChallengeRiskConfig()
        self._initial_balance: float = 0.0
        self._current_equity: float = 0.0
        self._start_of_day_equity: float = 0.0
        self._daily_pnl: float = 0.0
        self._last_date: Optional[date] = None
        self._daily_halted: bool = False
        self._permanently_halted: bool = False
        self._total_trades: int = 0
        self._start_date: Optional[date] = None
        self._current_date: Optional[date] = None
        self._consecutive_wins: int = 0

    def initialize(self, initial_balance: float):
        self._initial_balance = initial_balance
        self._current_equity = initial_balance
        self._start_of_day_equity = initial_balance
        self._daily_pnl = 0.0
        self._total_trades = 0
        self._daily_halted = False
        self._permanently_halted = False
        self._consecutive_wins = 0
        self._start_date = None
        self._current_date = None

    def on_bar(self, timestamp: datetime):
        current_date = timestamp.date()
        if self._start_date is None:
            self._start_date = current_date
        self._current_date = current_date

        if self._last_date is None:
            self._last_date = current_date

        # Daily reset
        if current_date > self._last_date:
            self._start_of_day_equity = self._current_equity
            self._daily_pnl = 0.0
            if not self._permanently_halted:
                self._daily_halted = False
            self._last_date = current_date

    def on_trade_closed(self, pnl: float, equity: float):
        self._current_equity = equity
        self._daily_pnl += pnl
        self._total_trades += 1

        if pnl > 0:
            self._consecutive_wins += 1
        else:
            self._consecutive_wins = 0

        # Check daily DD halt
        if self._start_of_day_equity > 0:
            daily_dd = (self._start_of_day_equity - equity) / self._start_of_day_equity
            if daily_dd >= self.config.daily_dd_halt:
                self._daily_halted = True

        # Check total DD halt
        if self._initial_balance > 0:
            total_dd = (self._initial_balance - equity) / self._initial_balance
            if total_dd >= self.config.dd_halt:
                self._permanently_halted = True
                self._daily_halted = True

    def get_risk_multiplier(self) -> float:
        """
        Returns a multiplier applied to base_risk.
        E.g., multiplier=1.75 with base_risk=0.02 → 3.5% risk per trade.
        """
        if self._permanently_halted or self._daily_halted:
            return 0.0

        c = self.config

        # Current state
        pnl_pct = self._profit_pct
        total_dd = self._total_dd_pct
        daily_dd = self._daily_dd_pct
        progress = self._target_progress     # 0.0 to 1.0+
        pace = self._pace_ratio              # >1.0 = ahead, <1.0 = behind

        # ── Priority 1: DD danger zone — survival mode ──
        if total_dd >= c.dd_danger_zone:
            return c.survival_risk_mult

        # ── Priority 2: Daily DD danger — reduce ──
        if daily_dd >= c.daily_dd_danger:
            return c.survival_risk_mult

        # ── Priority 3: Near target — coast ──
        if progress >= c.near_target_pct:
            mult = c.coast_risk_mult
            return self._apply_ramp_up(mult)

        # ── Priority 4: Pace-based risk ──
        if pace >= 1.5:
            # Way ahead of pace — protect gains
            mult = c.protect_risk_mult
        elif pace >= 0.8:
            # On pace — standard
            mult = c.standard_risk_mult
        else:
            # Behind pace — need to push
            # But only if we have DD headroom
            dd_headroom = c.dd_danger_zone - total_dd
            if dd_headroom > 0.03:
                mult = c.aggressive_risk_mult
            else:
                # Limited headroom — can't be too aggressive
                mult = c.standard_risk_mult

        # Slight bonus for hot streak (max +20%)
        if self._consecutive_wins >= 3:
            streak_bonus = 1.0 + 0.1 * min(self._consecutive_wins - 2, 2)
            mult *= streak_bonus

        return self._apply_ramp_up(mult)

    def _apply_ramp_up(self, mult: float) -> float:
        """Reduce risk on first few trades of the challenge."""
        if self._total_trades < self.config.ramp_up_trades:
            return mult * self.config.ramp_up_mult
        return mult

    def should_stop_trading(self) -> bool:
        return self._permanently_halted or self._daily_halted

    # ── State properties ──────────────────────────────────

    @property
    def _profit_pct(self) -> float:
        if self._initial_balance <= 0:
            return 0.0
        return (self._current_equity - self._initial_balance) / self._initial_balance

    @property
    def _total_dd_pct(self) -> float:
        if self._initial_balance <= 0:
            return 0.0
        return max(0, (self._initial_balance - self._current_equity) / self._initial_balance)

    @property
    def _daily_dd_pct(self) -> float:
        if self._start_of_day_equity <= 0:
            return 0.0
        return max(0, (self._start_of_day_equity - self._current_equity) / self._start_of_day_equity)

    @property
    def _target_progress(self) -> float:
        """How far toward the profit target (0.0 to 1.0+)."""
        target = self.config.profit_target
        if target <= 0:
            return 1.0
        return max(0, self._profit_pct / target)

    @property
    def _pace_ratio(self) -> float:
        """
        Progress / expected progress based on time elapsed.
        >1.0 = ahead of pace, <1.0 = behind.
        """
        days_elapsed = self._days_elapsed
        if days_elapsed <= 0:
            return 1.0  # First day — assume on pace

        max_days = self.config.max_calendar_days
        if max_days <= 0:
            return 1.0

        time_fraction = days_elapsed / max_days
        if time_fraction <= 0:
            return 1.0

        progress = self._target_progress
        return progress / time_fraction

    @property
    def _days_elapsed(self) -> int:
        if self._start_date is None or self._current_date is None:
            return 0
        return (self._current_date - self._start_date).days

    @property
    def _days_remaining(self) -> int:
        return max(0, self.config.max_calendar_days - self._days_elapsed)

    @property
    def current_dd_pct(self) -> float:
        return self._total_dd_pct

    @property
    def daily_dd_pct(self) -> float:
        return self._daily_dd_pct

    def get_current_zone(self) -> str:
        """Return the current risk zone name (for diagnostics)."""
        if self._permanently_halted:
            return "HALTED"
        if self._daily_halted:
            return "DAILY_HALT"
        if self._total_dd_pct >= self.config.dd_danger_zone:
            return "SURVIVAL"
        if self._daily_dd_pct >= self.config.daily_dd_danger:
            return "DAILY_DANGER"
        if self._target_progress >= self.config.near_target_pct:
            return "COAST"
        pace = self._pace_ratio
        if pace >= 1.5:
            return "PROTECT"
        if pace >= 0.8:
            return "STANDARD"
        return "AGGRESSIVE"
