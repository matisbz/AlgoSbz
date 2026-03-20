from dataclasses import dataclass, field
from datetime import timedelta, date
from typing import Optional

import numpy as np
import pandas as pd

from algosbz.backtest.results import BacktestResult
from algosbz.core.config import ChallengePhaseConfig


@dataclass
class PhaseResult:
    phase_name: str
    passed: bool
    reason: str
    profit_pct: float
    max_daily_dd_pct: float
    max_overall_dd_pct: float
    trading_days: int
    calendar_days: int
    days_to_target: Optional[int] = None


@dataclass
class ChallengeResult:
    phases: list[PhaseResult]
    overall_passed: bool

    @property
    def summary(self) -> str:
        lines = []
        for p in self.phases:
            status = "PASS" if p.passed else "FAIL"
            lines.append(f"{p.phase_name}: {status} — {p.reason}")
        overall = "PASSED" if self.overall_passed else "FAILED"
        lines.append(f"Overall: {overall}")
        return "\n".join(lines)


# ── Sequential Challenge Simulation ───────────────────────

@dataclass
class ChallengeAttempt:
    """One attempt at a single phase of a prop firm challenge."""
    phase_name: str
    attempt_number: int
    start_date: date
    end_date: date
    outcome: str          # PASS, FAIL_DAILY_DD, FAIL_TOTAL_DD, FAIL_TIME, INCOMPLETE
    profit_pct: float
    max_daily_dd_pct: float
    max_overall_dd_pct: float
    trading_days: int
    calendar_days: int
    trades_in_attempt: int
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


@dataclass
class SequentialChallengeResult:
    """Result of running sequential challenge attempts across the full data."""
    attempts: list[ChallengeAttempt]
    funded: bool
    funded_date: Optional[date]
    total_phase1_attempts: int
    total_phase2_attempts: int
    total_calendar_days: Optional[int]   # from first attempt start to funded date
    total_fees: float
    fee_per_attempt: float

    @property
    def total_attempts(self) -> int:
        return self.total_phase1_attempts + self.total_phase2_attempts

    @property
    def phase1_pass_rate(self) -> float:
        p1 = [a for a in self.attempts if a.phase_name == "Phase 1"]
        if not p1:
            return 0.0
        return sum(1 for a in p1 if a.outcome == "PASS") / len(p1) * 100

    @property
    def phase2_pass_rate(self) -> float:
        p2 = [a for a in self.attempts if a.phase_name == "Phase 2"]
        if not p2:
            return 0.0
        return sum(1 for a in p2 if a.outcome == "PASS") / len(p2) * 100

    @property
    def summary(self) -> str:
        lines = [
            f"Funded: {'YES' if self.funded else 'NO'}",
            f"Phase 1 attempts: {self.total_phase1_attempts} (pass rate: {self.phase1_pass_rate:.0f}%)",
            f"Phase 2 attempts: {self.total_phase2_attempts} (pass rate: {self.phase2_pass_rate:.0f}%)",
            f"Total fees: ${self.total_fees:,.0f}",
        ]
        if self.funded and self.total_calendar_days:
            lines.append(f"Days to funded: {self.total_calendar_days}")
        return "\n".join(lines)


class PropFirmSimulator:

    def __init__(self, phases: list[ChallengePhaseConfig]):
        self.phases = phases

    # ── Original methods (kept for backwards compat) ──────

    def evaluate(self, result: BacktestResult) -> ChallengeResult:
        phase_results = []
        for phase in self.phases:
            pr = self._evaluate_phase(result, phase)
            phase_results.append(pr)

        overall = all(p.passed for p in phase_results)
        return ChallengeResult(phases=phase_results, overall_passed=overall)

    def _evaluate_phase(self, result: BacktestResult, phase: ChallengePhaseConfig) -> PhaseResult:
        initial = result.initial_balance
        equity = result.equity_curve

        if equity.empty:
            return PhaseResult(
                phase_name=phase.name, passed=False, reason="No data",
                profit_pct=0, max_daily_dd_pct=0, max_overall_dd_pct=0,
                trading_days=0, calendar_days=0,
            )

        start_date = equity.index[0].date()
        end_date = equity.index[-1].date()
        calendar_days = (end_date - start_date).days + 1

        profit_pct = (equity.iloc[-1] - initial) / initial

        peak = equity.expanding().max()
        dd = (equity - peak) / peak
        max_overall_dd = abs(dd.min())

        daily_equity = equity.resample("1D").agg(["first", "min"]).dropna()
        max_daily_dd = 0.0
        if not daily_equity.empty:
            for _, row in daily_equity.iterrows():
                day_start = row["first"]
                day_low = row["min"]
                if day_start > 0:
                    daily_dd = (day_start - day_low) / day_start
                    max_daily_dd = max(max_daily_dd, daily_dd)

        trading_days = result.trading_days

        days_to_target = None
        target_equity = initial * (1 + phase.profit_target)
        for ts, val in equity.items():
            if val >= target_equity:
                days_to_target = (ts.date() - start_date).days + 1
                break

        reasons = []
        passed = True

        if max_daily_dd >= phase.daily_dd_limit:
            passed = False
            reasons.append(f"Daily DD {max_daily_dd:.2%} >= {phase.daily_dd_limit:.2%}")
        if max_overall_dd >= phase.max_dd_limit:
            passed = False
            reasons.append(f"Overall DD {max_overall_dd:.2%} >= {phase.max_dd_limit:.2%}")
        if profit_pct < phase.profit_target:
            passed = False
            reasons.append(f"Profit {profit_pct:.2%} < target {phase.profit_target:.2%}")
        if trading_days < phase.min_trading_days:
            passed = False
            reasons.append(f"Trading days {trading_days} < {phase.min_trading_days}")
        if calendar_days > phase.max_calendar_days:
            passed = False
            reasons.append(f"Calendar days {calendar_days} > {phase.max_calendar_days}")

        reason = "; ".join(reasons) if reasons else "All criteria met"

        return PhaseResult(
            phase_name=phase.name, passed=passed, reason=reason,
            profit_pct=profit_pct * 100,
            max_daily_dd_pct=max_daily_dd * 100,
            max_overall_dd_pct=max_overall_dd * 100,
            trading_days=trading_days,
            calendar_days=calendar_days,
            days_to_target=days_to_target,
        )

    # ── Sequential Challenge Simulation ───────────────────

    def sequential_challenge(
        self,
        result: BacktestResult,
        fee_per_attempt: float = 500.0,
        cooldown_days: int = 1,
    ) -> SequentialChallengeResult:
        """
        Simulate sequential prop firm challenge attempts on the real equity curve.

        Walks through the backtest chronologically:
        1. Start Phase 1 with $initial_balance
        2. Each bar: check daily DD, total DD, profit target, time limit
        3. FAIL (DD breach or time) → wait cooldown, restart same phase
        4. PASS (target + min days) → advance to next phase
        5. PASS all phases → FUNDED

        Uses the pre-computed equity curve, normalized to start from initial_balance
        at each attempt. This approximates correct position sizing since the backtest
        uses percentage-based risk (1% of equity).
        """
        if len(self.phases) == 0:
            return SequentialChallengeResult(
                attempts=[], funded=False, funded_date=None,
                total_phase1_attempts=0, total_phase2_attempts=0,
                total_calendar_days=None, total_fees=0, fee_per_attempt=fee_per_attempt,
            )

        equity = result.equity_curve
        if equity.empty:
            return SequentialChallengeResult(
                attempts=[], funded=False, funded_date=None,
                total_phase1_attempts=0, total_phase2_attempts=0,
                total_calendar_days=None, total_fees=0, fee_per_attempt=fee_per_attempt,
            )

        initial = result.initial_balance
        trades = result.trades
        all_attempts: list[ChallengeAttempt] = []

        current_phase_idx = 0
        phase_attempt_counts = [0] * len(self.phases)
        bar_idx = 0
        first_start_date = None
        funded = False
        funded_date = None

        while bar_idx < len(equity) and current_phase_idx < len(self.phases):
            phase = self.phases[current_phase_idx]
            phase_attempt_counts[current_phase_idx] += 1
            attempt_num = phase_attempt_counts[current_phase_idx]

            attempt = self._run_single_attempt(
                equity, trades, initial, phase, bar_idx, attempt_num,
            )
            all_attempts.append(attempt)

            if first_start_date is None:
                first_start_date = attempt.start_date

            # Find the bar index corresponding to the end date of this attempt
            end_ts = pd.Timestamp(attempt.end_date)
            # Advance past the attempt's end
            while bar_idx < len(equity) and equity.index[bar_idx].date() <= attempt.end_date:
                bar_idx += 1

            # Apply cooldown
            if attempt.outcome != "PASS":
                cooldown_target = attempt.end_date + timedelta(days=cooldown_days)
                while bar_idx < len(equity) and equity.index[bar_idx].date() < cooldown_target:
                    bar_idx += 1

            if attempt.outcome == "PASS":
                current_phase_idx += 1
                if current_phase_idx >= len(self.phases):
                    funded = True
                    funded_date = attempt.end_date
            else:
                # FAIL at any phase → back to Phase 1 (buy new exam)
                current_phase_idx = 0

        total_calendar_days = None
        if funded and first_start_date and funded_date:
            total_calendar_days = (funded_date - first_start_date).days + 1

        total_fees = sum(phase_attempt_counts) * fee_per_attempt

        return SequentialChallengeResult(
            attempts=all_attempts,
            funded=funded,
            funded_date=funded_date,
            total_phase1_attempts=phase_attempt_counts[0] if len(phase_attempt_counts) > 0 else 0,
            total_phase2_attempts=phase_attempt_counts[1] if len(phase_attempt_counts) > 1 else 0,
            total_calendar_days=total_calendar_days,
            total_fees=total_fees,
            fee_per_attempt=fee_per_attempt,
        )

    def _run_single_attempt(
        self,
        equity: pd.Series,
        trades: list,
        initial: float,
        phase: ChallengePhaseConfig,
        start_bar: int,
        attempt_num: int,
    ) -> ChallengeAttempt:
        """
        Simulate one challenge attempt starting from start_bar.

        Normalizes equity so the attempt starts at initial_balance.
        Walks forward bar by bar checking DD limits and profit target.
        """
        start_equity = equity.iloc[start_bar]
        if start_equity <= 0:
            start_equity = initial
        scale = initial / start_equity

        start_date = equity.index[start_bar].date()
        max_end_date = start_date + timedelta(days=phase.max_calendar_days)

        # Track state
        max_daily_dd = 0.0
        max_overall_dd = 0.0
        current_day = start_date
        day_start_equity = initial
        target_equity = initial * (1 + phase.profit_target)
        target_reached = False
        target_reached_date = None

        # Collect trading days in this window
        attempt_trade_dates = set()
        for t in trades:
            t_date = t.entry_time.date()
            if start_date <= t_date <= max_end_date:
                attempt_trade_dates.add(t_date)

        # Normalized equity points for this attempt
        attempt_equity_points = []
        outcome = "INCOMPLETE"
        end_date = start_date
        end_bar = start_bar

        for i in range(start_bar, len(equity)):
            bar_date = equity.index[i].date()

            # Time limit check
            if bar_date > max_end_date:
                break

            normalized = equity.iloc[i] * scale
            attempt_equity_points.append((equity.index[i], normalized))
            end_date = bar_date
            end_bar = i

            # Daily reset
            if bar_date > current_day:
                current_day = bar_date
                # Use last bar of previous day as day start
                day_start_equity = normalized

            # Overall DD from initial balance (static, like prop firms)
            overall_dd = max(0, (initial - normalized) / initial)
            max_overall_dd = max(max_overall_dd, overall_dd)

            # Daily DD from start of day
            if day_start_equity > 0:
                daily_dd = max(0, (day_start_equity - normalized) / day_start_equity)
                max_daily_dd = max(max_daily_dd, daily_dd)
            else:
                daily_dd = 0.0

            # Check DD breach → immediate FAIL
            if overall_dd >= phase.max_dd_limit:
                outcome = "FAIL_TOTAL_DD"
                break

            if daily_dd >= phase.daily_dd_limit:
                outcome = "FAIL_DAILY_DD"
                break

            # Check profit target
            if not target_reached and normalized >= target_equity:
                target_reached = True
                target_reached_date = bar_date

            # Early exit: target reached AND min trading days met → PASS
            if target_reached:
                trading_days_so_far = len({
                    d for d in attempt_trade_dates
                    if start_date <= d <= bar_date
                })
                if trading_days_so_far >= phase.min_trading_days:
                    outcome = "PASS"
                    break

        # If we exited the loop without explicit outcome
        if outcome == "INCOMPLETE":
            if target_reached:
                # Target was reached but not enough trading days before data ran out
                trading_days_in_attempt = len({
                    d for d in attempt_trade_dates
                    if start_date <= d <= end_date
                })
                if trading_days_in_attempt >= phase.min_trading_days:
                    outcome = "PASS"
            elif end_date >= max_end_date:
                outcome = "FAIL_TIME"

        calendar_days = (end_date - start_date).days + 1
        trading_days_count = len({
            d for d in attempt_trade_dates
            if start_date <= d <= end_date
        })

        # Final profit
        if attempt_equity_points:
            final_equity = attempt_equity_points[-1][1]
        else:
            final_equity = initial
        profit_pct = (final_equity - initial) / initial * 100

        # Build equity series for this attempt
        if attempt_equity_points:
            times, values = zip(*attempt_equity_points)
            eq_series = pd.Series(values, index=pd.DatetimeIndex(times), name="equity")
        else:
            eq_series = pd.Series(dtype=float, name="equity")

        return ChallengeAttempt(
            phase_name=phase.name,
            attempt_number=attempt_num,
            start_date=start_date,
            end_date=end_date,
            outcome=outcome,
            profit_pct=profit_pct,
            max_daily_dd_pct=max_daily_dd * 100,
            max_overall_dd_pct=max_overall_dd * 100,
            trading_days=trading_days_count,
            calendar_days=calendar_days,
            trades_in_attempt=len({
                d for d in attempt_trade_dates
                if start_date <= d <= end_date
            }),
            equity_curve=eq_series,
        )

    # ── Rolling Simulation (kept) ─────────────────────────

    def rolling_simulation(
        self,
        result: BacktestResult,
        phase: ChallengePhaseConfig,
        window_days: int = 30,
        step_days: int = 5,
    ) -> list[PhaseResult]:
        """Run the challenge evaluation over rolling windows."""
        equity = result.equity_curve
        if equity.empty:
            return []

        start = equity.index[0]
        end = equity.index[-1]
        window = timedelta(days=window_days)
        step = timedelta(days=step_days)

        results = []
        current_start = start

        while current_start + window <= end:
            window_end = current_start + window
            window_equity = equity[(equity.index >= current_start) & (equity.index < window_end)]

            if window_equity.empty:
                current_start += step
                continue

            scale = result.initial_balance / window_equity.iloc[0]
            normalized = window_equity * scale

            window_trades = [
                t for t in result.trades
                if current_start <= pd.Timestamp(t.entry_time) < window_end
            ]

            window_result = BacktestResult(
                trades=window_trades,
                equity_curve=normalized,
                initial_balance=result.initial_balance,
            )

            pr = self._evaluate_phase(window_result, phase)
            results.append(pr)
            current_start += step

        return results

    # ── Monte Carlo (kept) ────────────────────────────────

    def monte_carlo(
        self,
        result: BacktestResult,
        phase: ChallengePhaseConfig,
        n_simulations: int = 1000,
        max_trades_per_sim: int = 60,
    ) -> dict:
        """
        Monte Carlo with block sampling + equity management simulation.
        Uses NORMALIZED trade PnLs (percentage of equity at entry) to avoid
        compounding bias.
        """
        if not result.trades:
            return {
                "pass_rate": 0.0, "n_simulations": 0, "avg_profit": 0.0,
                "avg_max_dd": 0.0, "median_profit": 0.0,
                "p5_profit": 0.0, "p95_profit": 0.0,
                "fail_breakdown": {"daily_dd": 0, "total_dd": 0, "profit": 0},
            }

        initial = result.initial_balance
        equity_curve = result.equity_curve

        trade_pct_pnls = []
        for t in result.trades:
            entry_ts = pd.Timestamp(t.entry_time)
            mask = equity_curve.index <= entry_ts
            if mask.any():
                equity_at_entry = equity_curve[mask].iloc[-1]
            else:
                equity_at_entry = initial
            if equity_at_entry > 0:
                trade_pct_pnls.append(t.pnl / equity_at_entry)
            else:
                trade_pct_pnls.append(0.0)

        trade_pcts = np.array(trade_pct_pnls)
        n_total = len(trade_pcts)

        block_size = min(5, n_total)
        n_trades = min(n_total, max_trades_per_sim)
        n_blocks = max(1, n_trades // block_size)

        dd_tiers = [(0.03, 1.0), (0.05, 0.5), (0.07, 0.25), (0.08, 0.0)]
        daily_stop = 0.035

        passes = 0
        profits = []
        max_dds = []
        fail_reasons = {"daily_dd": 0, "total_dd": 0, "profit": 0}

        rng = np.random.default_rng(42)

        for _ in range(n_simulations):
            equity = initial
            day_start = initial
            max_dd = 0.0
            max_daily_dd = 0.0
            trade_count = 0
            daily_halted = False

            for _ in range(n_blocks):
                start = rng.integers(0, max(1, n_total - block_size + 1))
                block = trade_pcts[start: start + block_size]

                for pct_pnl in block:
                    dd_from_initial = max(0, (initial - equity) / initial)
                    multiplier = 0.0
                    for threshold, mult in dd_tiers:
                        if dd_from_initial < threshold:
                            multiplier = mult
                            break

                    daily_dd = max(0, (day_start - equity) / day_start) if day_start > 0 else 0
                    if daily_dd >= daily_stop:
                        daily_halted = True

                    if multiplier <= 0 or daily_halted:
                        trade_count += 1
                        if trade_count % 4 == 0:
                            max_daily_dd = max(max_daily_dd, daily_dd)
                            day_start = equity
                            daily_halted = False
                        continue

                    pnl = equity * pct_pnl * multiplier
                    equity += pnl
                    trade_count += 1

                    dd = max(0, (initial - equity) / initial)
                    max_dd = max(max_dd, dd)

                    if trade_count % 4 == 0:
                        daily_dd = max(0, (day_start - equity) / day_start) if day_start > 0 else 0
                        max_daily_dd = max(max_daily_dd, daily_dd)
                        day_start = equity
                        daily_halted = False

            profit_pct = (equity - initial) / initial
            profits.append(profit_pct)
            max_dds.append(max_dd)

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
            "avg_max_dd": float(max_dds_arr.mean() * 100),
            "median_profit": float(np.median(profits_arr) * 100),
            "p5_profit": float(np.percentile(profits_arr, 5) * 100),
            "p95_profit": float(np.percentile(profits_arr, 95) * 100),
            "fail_breakdown": {
                k: v / n_simulations * 100 for k, v in fail_reasons.items()
            },
        }
