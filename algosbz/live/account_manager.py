"""
Account Manager — tracks account state, DD, phase transitions, and portfolio controls.

Each AccountState object represents one prop firm account and enforces:
- Daily loss cap (our control, tighter than FTMO's 5%)
- Per-combo cooldown
- Max instrument trades per day
- Max daily losses
- Phase transition (P1→P2→Funded) with config switch
"""
import logging
from datetime import date, datetime
from collections import defaultdict
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class AccountState:
    """Tracks live state of a single prop firm account."""

    def __init__(self, name: str, config: dict, mode_configs: dict):
        self.name = name
        self.login = config["login"]
        self.password = config["password"]
        self.server = config["server"]
        self.state = config["state"]  # phase1 | phase2 | funded
        self.initial_balance = config["initial_balance"]
        self.enabled = config.get("enabled", True)
        self.start_date = config.get("start_date", "")

        # Mode configs (exam vs funded)
        self.mode_configs = mode_configs

        # Daily tracking (reset each day)
        self._current_day: Optional[date] = None
        self._day_start_equity: float = self.initial_balance
        self._combo_day_losses: dict[str, int] = defaultdict(int)
        self._instr_day_trades: dict[str, int] = defaultdict(int)
        self._total_daily_losses: int = 0
        self._daily_stopped: bool = False

        # Cumulative tracking
        self.current_equity: float = self.initial_balance
        self.trading_days: int = 0
        self.total_trades: int = 0
        self.total_pnl: float = 0.0

    @property
    def active_config(self) -> dict:
        """Return the right config for current state."""
        if self.state == "funded":
            return self.mode_configs["funded_mode"]
        return self.mode_configs["exam_mode"]

    @property
    def risk_per_trade(self) -> float:
        """Effective risk per trade for current state + phase."""
        cfg = self.active_config
        risk = cfg["risk_per_trade"]
        # In phase2, apply P2 risk factor (only in exam mode)
        if self.state == "phase2" and "p2_risk_factor" in cfg:
            risk *= cfg["p2_risk_factor"]
        return risk

    @property
    def daily_cap_pct(self) -> float:
        return self.active_config["daily_cap_pct"]

    @property
    def cooldown(self) -> int:
        return self.active_config["cooldown"]

    @property
    def max_instr_per_day(self) -> int:
        return self.active_config["max_instr_per_day"]

    @property
    def max_daily_losses(self) -> int:
        return self.active_config["max_daily_losses"]

    def new_day(self, equity: float):
        """Reset daily counters. Called when a new trading day starts."""
        # Count trading day if we traded yesterday
        if self._current_day is not None and self.total_trades > 0:
            if sum(self._instr_day_trades.values()) > 0:
                self.trading_days += 1
        self._current_day = date.today()
        self._day_start_equity = equity
        self.current_equity = equity
        self._combo_day_losses.clear()
        self._instr_day_trades.clear()
        self._total_daily_losses = 0
        self._daily_stopped = False
        logger.info("[%s] New day. Equity=%.2f, State=%s, TradingDays=%d",
                    self.name, equity, self.state, self.trading_days)

    def can_trade(self, combo_name: str, instrument: str) -> tuple[bool, str]:
        """Check if a trade is allowed given portfolio controls."""
        if not self.enabled:
            return False, "account disabled"

        if self._daily_stopped:
            return False, f"daily cap hit ({self.daily_cap_pct}%)"

        if self._combo_day_losses[combo_name] >= self.cooldown:
            return False, f"combo cooldown ({combo_name}: {self._combo_day_losses[combo_name]} losses)"

        if self._instr_day_trades[instrument] >= self.max_instr_per_day:
            return False, f"max instrument trades ({instrument}: {self._instr_day_trades[instrument]})"

        if self._total_daily_losses >= self.max_daily_losses:
            return False, f"max daily losses ({self._total_daily_losses})"

        # Check FTMO hard limits BEFORE trading
        # Total DD from initial: max 10%
        total_dd_pct = (self.initial_balance - self.current_equity) / self.initial_balance * 100
        if total_dd_pct >= 9.0:  # stop at 9% to leave buffer
            return False, f"approaching total DD limit ({total_dd_pct:.1f}%)"

        # Daily DD from start of day: max 5%
        daily_dd_pct = (self._day_start_equity - self.current_equity) / self.initial_balance * 100
        if daily_dd_pct >= 4.0:  # stop at 4% to leave buffer
            return False, f"approaching daily DD limit ({daily_dd_pct:.1f}%)"

        return True, "ok"

    def on_trade_opened(self, combo_name: str, instrument: str):
        """Record that a trade was opened."""
        self._instr_day_trades[instrument] += 1
        self.total_trades += 1
        logger.info("[%s] Trade opened: %s (%s). Instr trades today: %d",
                    self.name, combo_name, instrument,
                    self._instr_day_trades[instrument])

    def on_trade_closed(self, combo_name: str, pnl: float):
        """Record trade result and check controls."""
        self.current_equity += pnl
        self.total_pnl += pnl

        if pnl < 0:
            self._combo_day_losses[combo_name] += 1
            self._total_daily_losses += 1

        # Check our daily cap
        daily_loss_pct = (self._day_start_equity - self.current_equity) / self.initial_balance * 100
        if daily_loss_pct >= self.daily_cap_pct:
            self._daily_stopped = True
            logger.warning("[%s] DAILY CAP HIT: %.1f%% loss. No more trades today.",
                           self.name, daily_loss_pct)

        logger.info("[%s] Trade closed: %s PnL=%.2f Equity=%.2f DailyDD=%.2f%%",
                    self.name, combo_name, pnl, self.current_equity, daily_loss_pct)

    def check_phase_transition(self) -> Optional[str]:
        """
        Check if account should transition to next phase.
        Returns new state if transition needed, None otherwise.
        """
        if self.state == "phase1":
            profit_pct = (self.current_equity - self.initial_balance) / self.initial_balance * 100
            if profit_pct >= 10.0 and self.trading_days >= 4:
                logger.info("[%s] PHASE 1 PASSED! Profit=%.1f%%, Days=%d",
                            self.name, profit_pct, self.trading_days)
                self.state = "phase2"
                # Reset for P2 (balance resets in FTMO)
                self.current_equity = self.initial_balance
                self.trading_days = 0
                self.total_pnl = 0.0
                return "phase2"

        elif self.state == "phase2":
            profit_pct = (self.current_equity - self.initial_balance) / self.initial_balance * 100
            if profit_pct >= 5.0 and self.trading_days >= 4:
                logger.info("[%s] PHASE 2 PASSED! FUNDED! Profit=%.1f%%, Days=%d",
                            self.name, profit_pct, self.trading_days)
                self.state = "funded"
                # Reset for funded account
                self.current_equity = self.initial_balance
                self.trading_days = 0
                self.total_pnl = 0.0
                return "funded"

        return None

    def status_line(self) -> str:
        """One-line status for logging."""
        profit_pct = (self.current_equity - self.initial_balance) / self.initial_balance * 100
        daily_dd = (self._day_start_equity - self.current_equity) / self.initial_balance * 100
        total_dd = (self.initial_balance - self.current_equity) / self.initial_balance * 100
        target = 10.0 if self.state == "phase1" else (5.0 if self.state == "phase2" else 0.0)
        return (f"[{self.name}] {self.state.upper():>7s} | "
                f"Eq={self.current_equity:>10,.0f} ({profit_pct:>+5.1f}%) | "
                f"DailyDD={daily_dd:>4.1f}% TotalDD={total_dd:>4.1f}% | "
                f"Target={'%.0f%%' % target if target > 0 else 'N/A':>5s} | "
                f"Trades={self.total_trades}")


def load_accounts(config_path: str) -> tuple[list[AccountState], dict]:
    """Load accounts and config from YAML file."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    mode_configs = {
        "exam_mode": raw["exam_mode"],
        "funded_mode": raw["funded_mode"],
    }

    accounts = []
    for acct_cfg in raw["accounts"]:
        if acct_cfg.get("enabled", True) and acct_cfg["login"] != 0:
            accounts.append(AccountState(acct_cfg["name"], acct_cfg, mode_configs))

    return accounts, raw


def save_account_states(config_path: str, accounts: list[AccountState]):
    """Update account states back to YAML (only the state field)."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    state_map = {a.name: a.state for a in accounts}
    for acct_cfg in raw["accounts"]:
        if acct_cfg["name"] in state_map:
            acct_cfg["state"] = state_map[acct_cfg["name"]]

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, default_flow_style=False, sort_keys=False)
