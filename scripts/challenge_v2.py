"""
Challenge simulation v2 — advanced risk management within 30-day windows.

Tests multiple smart risk strategies:
1. BASELINE: Static risk, no lock
2. PROFIT_LOCK: Stop trading at target
3. STEP_DOWN: Start high risk, reduce when ahead
4. AGGRESSIVE_LOCK: Higher initial risk + aggressive lock

The key insight: we need to BOTH generate enough profit AND avoid DD,
while maintaining 4+ trading days. These goals conflict at static risk.
Dynamic risk resolves this by being aggressive early and protective later.

Usage:
    python -X utf8 scripts/challenge_v2.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import timedelta
from collections import defaultdict

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

DECK = {
    "VMR_USDCHF_H1": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDCHF",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "SwBrk_XTIUSD_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4"},
    },
    "TPB_XTIUSD_H4": {
        "module": "algosbz.strategy.trend_pullback",
        "class": "TrendPullback",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "VMR_USDJPY_H4": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDJPY",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
}


def load_strategy(entry):
    import importlib
    mod = importlib.import_module(entry["module"])
    cls = getattr(mod, entry["class"])
    return cls(entry["params"])


def collect_window_data(config, instruments, data_dict, start_date, risk_pct,
                        window_days=30):
    """
    Run all combos and collect trades + equity points.
    Returns (all_trades, combined_equity, initial_balance).
    """
    end_date = start_date + timedelta(days=window_days)
    initial = config.account.initial_balance

    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )

    all_trades = []
    all_equity_points = []

    for combo_name, entry in DECK.items():
        sym = entry["symbol"]
        if sym not in data_dict:
            continue

        full_data = data_dict[sym]
        lookback = timedelta(days=120)
        slice_start = start_date - lookback
        mask = (full_data.index >= pd.Timestamp(slice_start)) & (
            full_data.index < pd.Timestamp(end_date)
        )
        window_data = full_data[mask]
        if window_data.empty:
            continue

        inst_cfg = instruments.get(sym)
        if inst_cfg is None:
            continue

        try:
            strategy = load_strategy(entry)
            engine = BacktestEngine(cfg, inst_cfg, EquityManager(eq_cfg))
            result = engine.run(strategy, window_data, sym)
        except Exception:
            continue

        for t in result.trades:
            t_ts = t.entry_time
            if isinstance(t_ts, pd.Timestamp):
                t_date = t_ts.date()
            elif hasattr(t_ts, 'date'):
                t_date = t_ts.date()
            else:
                t_date = t_ts
            if start_date.date() <= t_date < end_date.date():
                all_trades.append({
                    "ts": t_ts,
                    "date": t_date,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl / initial * 100,
                    "combo": combo_name,
                })

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                all_equity_points.append((ts, val - initial))

    if not all_equity_points:
        return None, None, initial

    all_trades.sort(key=lambda x: x["ts"])

    df = pd.DataFrame(all_equity_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    return all_trades, combined_equity, initial


def evaluate_window(all_trades, combined_equity, initial, strategy_fn):
    """
    Evaluate a window with a given risk strategy function.

    strategy_fn(trades_so_far, equity_pct, trading_days) -> action
    Actions: "TRADE" (continue), "LOCK" (stop trading), "REDUCE" (halve PnL)

    For simplicity, LOCK means we stop counting trades and freeze equity.
    REDUCE means subsequent trade PnLs are halved (simulating reduced position).
    """
    if all_trades is None or combined_equity is None:
        return {"outcome": "NO_DATA", "profit_pct": 0, "max_dd": 0,
                "max_daily_dd": 0, "trades": 0, "trading_days": 0,
                "action_taken": None}

    # Walk through trades chronologically, applying strategy
    running_pnl = 0
    trading_days = set()
    counted_trades = []
    lock_ts = None
    action_taken = None
    risk_mult = 1.0

    for i, t in enumerate(all_trades):
        # Current state before this trade
        equity_pct = running_pnl / initial * 100

        action = strategy_fn(counted_trades, equity_pct, len(trading_days), i)

        if action == "LOCK":
            lock_ts = t["ts"]
            action_taken = f"LOCKED at {equity_pct:+.1f}%"
            break
        elif action == "REDUCE":
            risk_mult = 0.5
        elif action == "MICRO":
            risk_mult = 0.25
        else:
            risk_mult = 1.0

        # Apply trade with risk multiplier
        adjusted_pnl = t["pnl"] * risk_mult
        running_pnl += adjusted_pnl
        trading_days.add(t["date"])
        counted_trades.append({**t, "adjusted_pnl": adjusted_pnl})

    # Calculate DD from equity curve (up to lock point)
    if lock_ts:
        eq = combined_equity[combined_equity.index <= lock_ts]
    else:
        eq = combined_equity

    max_dd = 0
    for val in eq:
        dd = (initial - val) / initial
        max_dd = max(max_dd, dd)

    max_daily_dd = 0
    if len(eq) > 0:
        daily = eq.resample("1D").agg(["first", "min"]).dropna()
        for _, row in daily.iterrows():
            if row["first"] > 0:
                ddd = (row["first"] - row["min"]) / row["first"]
                max_daily_dd = max(max_daily_dd, ddd)

    # For reduced-risk strategies, we need to recalculate the actual equity
    # based on adjusted PnLs (the equity curve was computed at full risk)
    if risk_mult != 1.0 or any(t.get("adjusted_pnl") != t["pnl"] for t in counted_trades):
        # Recalculate profit from adjusted trades
        profit_pct = sum(t["adjusted_pnl"] for t in counted_trades) / initial * 100
    else:
        if lock_ts:
            profit_pct = (eq.iloc[-1] - initial) / initial * 100
        else:
            profit_pct = (combined_equity.iloc[-1] - initial) / initial * 100

    # Determine outcome
    if max_dd >= 0.10:
        outcome = "FAIL_DD"
    elif max_daily_dd >= 0.05:
        outcome = "FAIL_DAILY_DD"
    elif profit_pct >= 8.0 and len(trading_days) >= 4:
        outcome = "PASS"
    elif profit_pct >= 8.0:
        outcome = "FAIL_MIN_DAYS"
    else:
        outcome = "FAIL_PROFIT"

    return {
        "outcome": outcome,
        "profit_pct": round(profit_pct, 2),
        "max_dd": round(max_dd * 100, 2),
        "max_daily_dd": round(max_daily_dd * 100, 2),
        "trades": len(counted_trades),
        "trading_days": len(trading_days),
        "action_taken": action_taken,
    }


# === STRATEGY FUNCTIONS ===

def strategy_baseline(trades, equity_pct, trading_days, trade_idx):
    """No intervention — static risk."""
    return "TRADE"


def strategy_profit_lock(trades, equity_pct, trading_days, trade_idx):
    """Lock profits once target is hit with enough trading days."""
    if equity_pct >= 8.0 and trading_days >= 4:
        return "LOCK"
    return "TRADE"


def strategy_step_down(trades, equity_pct, trading_days, trade_idx):
    """Start aggressive, step down when ahead, lock at target."""
    if equity_pct >= 8.0 and trading_days >= 4:
        return "LOCK"
    if equity_pct >= 5.0:
        return "REDUCE"  # Protect gains
    return "TRADE"


def strategy_aggressive_lock(trades, equity_pct, trading_days, trade_idx):
    """
    Aggressive early, very protective when ahead.
    Lock at 8% with 4 days. Go micro at 6%+.
    """
    if equity_pct >= 8.0 and trading_days >= 4:
        return "LOCK"
    if equity_pct >= 6.0:
        return "MICRO"  # Very small trades just for min days
    if equity_pct >= 4.0:
        return "REDUCE"
    return "TRADE"


def strategy_dd_aware(trades, equity_pct, trading_days, trade_idx):
    """
    Reduce risk when we're losing to avoid DD.
    Lock when target is met.
    """
    if equity_pct >= 8.0 and trading_days >= 4:
        return "LOCK"
    if equity_pct >= 5.0:
        return "REDUCE"
    if equity_pct <= -4.0:
        return "MICRO"  # Almost at DD limit, go tiny
    if equity_pct <= -2.0:
        return "REDUCE"  # Starting to lose, pull back
    return "TRADE"


STRATEGIES = {
    "Baseline": strategy_baseline,
    "ProfitLock": strategy_profit_lock,
    "StepDown": strategy_step_down,
    "AggrLock": strategy_aggressive_lock,
    "DD_Aware": strategy_dd_aware,
}


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    symbols = list({e["symbol"] for e in DECK.values()})
    data_dict = {}
    print("Loading data...")
    for sym in symbols:
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    window_starts = pd.date_range("2015-01-01", "2024-11-01", freq="30D")
    print(f"\n  Windows: {len(window_starts)} x 30 days")

    # Pre-compute window data at each risk level
    for base_risk in [0.02, 0.03]:
        print(f"\n{'='*110}")
        print(f"  BASE RISK: {base_risk*100:.0f}% — Testing {len(STRATEGIES)} risk strategies")
        print(f"{'='*110}")

        # Collect all window data once
        print(f"\n  Pre-computing {len(window_starts)} windows...", end=" ", flush=True)
        window_cache = []
        for i, start in enumerate(window_starts):
            trades, equity, initial = collect_window_data(
                config, instruments, data_dict, start, base_risk
            )
            window_cache.append((trades, equity, initial))
            if (i + 1) % 30 == 0:
                print(".", end="", flush=True)
        print(" done")

        # Test each strategy
        all_results = []
        for strat_name, strat_fn in STRATEGIES.items():
            outcomes = defaultdict(int)
            profits = []
            locks = 0

            for trades, equity, initial in window_cache:
                r = evaluate_window(trades, equity, initial, strat_fn)
                outcomes[r["outcome"]] += 1
                profits.append(r["profit_pct"])
                if r["action_taken"]:
                    locks += 1

            n = len(window_starts)
            passes = outcomes["PASS"]
            fail_dd = outcomes["FAIL_DD"] + outcomes["FAIL_DAILY_DD"]
            fail_profit = outcomes["FAIL_PROFIT"]
            fail_days = outcomes["FAIL_MIN_DAYS"]
            pass_rate = passes / n * 100

            print(f"\n  {strat_name:15s}: PASS {passes:>3d}/{n} ({pass_rate:>5.1f}%) | "
                  f"FailDD {fail_dd:>3d} | FailProfit {fail_profit:>3d} | "
                  f"FailDays {fail_days:>3d} | AvgPr {np.mean(profits):>+6.2f}% | "
                  f"Locked {locks:>3d}")

            all_results.append({
                "strategy": strat_name,
                "risk": f"{base_risk*100:.0f}%",
                "passes": passes,
                "pass_rate": pass_rate,
                "fail_dd": fail_dd,
                "fail_profit": fail_profit,
                "fail_days": fail_days,
                "avg_profit": np.mean(profits),
                "locked": locks,
            })

        # Best result for this risk level
        best = max(all_results[-len(STRATEGIES):], key=lambda x: x["pass_rate"])
        print(f"\n  BEST @ {base_risk*100:.0f}%: {best['strategy']} "
              f"with {best['pass_rate']:.1f}% pass rate")

    # Final ROI analysis
    print(f"\n{'='*110}")
    print(f"  ROI ANALYSIS — €80/exam, $10K accounts")
    print(f"{'='*110}")
    for r in all_results:
        p1 = r["pass_rate"] / 100
        if p1 > 0:
            funded = p1 * 0.60  # P2 estimate
            cost = 80 / funded if funded > 0 else float("inf")
            monthly_income = 10 * funded * 400  # 10 exams, $400/funded/month
            print(f"  {r['strategy']:15s} @{r['risk']:>3s}: "
                  f"P1={p1*100:5.1f}% → {funded*100:5.1f}% funded/exam "
                  f"(€{cost:,.0f}/funded) | "
                  f"10 exams → €{monthly_income:,.0f}/mo")


if __name__ == "__main__":
    main()
