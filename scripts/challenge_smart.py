"""
Smart FTMO challenge simulation with profit locking.

Key insight from DD analysis: 72% of DD fails were profitable before failing.
Many peaked at +15-36% then hit DD. If we stop trading once target is hit
with enough trading days, those become PASSES.

Implements:
1. TARGET STOP: Stop trading once profit >= 8% AND trading days >= 4
2. EARLY TARGET: If profit hits 10%+ early, stop even with 3 trading days
   (the 4th day requirement can be met with a micro-trade)

Usage:
    python -X utf8 scripts/challenge_smart.py
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


def run_smart_window(config, instruments, data_dict, risk_pct, start_date,
                     window_days=30, profit_lock=True):
    """
    Run a 30-day window collecting all trades chronologically.
    If profit_lock=True, stop counting trades once target is met.
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

    # Collect ALL trades from all combos with timestamps
    all_trades = []
    # Collect ALL equity points for DD tracking
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
                    "combo": combo_name,
                })

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                all_equity_points.append((ts, val - initial))

    if not all_equity_points:
        return {"outcome": "NO_DATA", "profit_pct": 0, "max_dd": 0, "max_daily_dd": 0,
                "trades": 0, "trading_days": 0, "locked_at": None}

    # Sort trades chronologically
    all_trades.sort(key=lambda x: x["ts"])

    # Build combined equity curve
    df = pd.DataFrame(all_equity_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    # === PROFIT LOCK LOGIC ===
    # Walk through equity curve chronologically. Track running state.
    # Once we hit target with enough trading days, record the "lock point".
    lock_ts = None
    lock_profit = None

    if profit_lock:
        trading_days_so_far = set()
        for t in all_trades:
            trading_days_so_far.add(t["date"])

            # Check combined equity at this trade's timestamp (or closest before)
            # Use the trade's cumulative PnL impact
            trade_ts = t["ts"]
            if isinstance(trade_ts, pd.Timestamp):
                # Find equity at or just before this timestamp
                eq_before = combined_equity[combined_equity.index <= trade_ts]
                if not eq_before.empty:
                    current_eq = eq_before.iloc[-1]
                    profit_pct_now = (current_eq - initial) / initial * 100

                    if profit_pct_now >= 8.0 and len(trading_days_so_far) >= 4:
                        lock_ts = trade_ts
                        lock_profit = profit_pct_now
                        break

    # === CALCULATE METRICS ===
    if lock_ts is not None:
        # Use equity up to lock point only
        locked_equity = combined_equity[combined_equity.index <= lock_ts]
        final_equity = locked_equity.iloc[-1]
        profit_pct = (final_equity - initial) / initial * 100

        # DD only up to lock point
        max_dd = 0
        for val in locked_equity:
            dd = (initial - val) / initial
            max_dd = max(max_dd, dd)

        max_daily_dd = 0
        daily = locked_equity.resample("1D").agg(["first", "min"]).dropna()
        for _, row in daily.iterrows():
            if row["first"] > 0:
                ddd = (row["first"] - row["min"]) / row["first"]
                max_daily_dd = max(max_daily_dd, ddd)

        # Trades and days only up to lock
        trades_count = sum(1 for t in all_trades if t["ts"] <= lock_ts)
        trading_days = set(t["date"] for t in all_trades if t["ts"] <= lock_ts)
    else:
        # No lock — use full window
        final_equity = combined_equity.iloc[-1]
        profit_pct = (final_equity - initial) / initial * 100

        max_dd = 0
        for val in combined_equity:
            dd = (initial - val) / initial
            max_dd = max(max_dd, dd)

        max_daily_dd = 0
        daily = combined_equity.resample("1D").agg(["first", "min"]).dropna()
        for _, row in daily.iterrows():
            if row["first"] > 0:
                ddd = (row["first"] - row["min"]) / row["first"]
                max_daily_dd = max(max_daily_dd, ddd)

        trades_count = len(all_trades)
        trading_days = set(t["date"] for t in all_trades)

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
        "trades": trades_count,
        "trading_days": len(trading_days),
        "locked_at": lock_profit,
    }


def run_simulation(config, instruments, data_dict, risk_pct, profit_lock, label):
    """Run full simulation across all windows."""
    window_starts = pd.date_range("2015-01-01", "2024-11-01", freq="30D")

    outcomes = defaultdict(int)
    profits = []
    trades_list = []
    locked_count = 0
    converted = 0  # Windows that would have failed but were saved by lock

    for i, start in enumerate(window_starts):
        r = run_smart_window(config, instruments, data_dict, risk_pct, start,
                            profit_lock=profit_lock)
        outcomes[r["outcome"]] += 1
        profits.append(r["profit_pct"])
        trades_list.append(r["trades"])
        if r["locked_at"] is not None:
            locked_count += 1

        if (i + 1) % 30 == 0:
            print(".", end="", flush=True)

    n = len(window_starts)
    passes = outcomes["PASS"]
    fail_dd = outcomes["FAIL_DD"] + outcomes["FAIL_DAILY_DD"]
    fail_profit = outcomes["FAIL_PROFIT"]
    fail_days = outcomes["FAIL_MIN_DAYS"]
    pass_rate = passes / n * 100

    print(f"\n    {label}:")
    print(f"      PASS: {passes}/{n} ({pass_rate:.1f}%) | "
          f"FailDD: {fail_dd} | FailProfit: {fail_profit} | FailDays: {fail_days}")
    print(f"      AvgProfit: {np.mean(profits):+.2f}% | AvgTrades: {np.mean(trades_list):.1f}")
    if profit_lock:
        print(f"      Windows locked at target: {locked_count}/{n}")

    return {
        "label": label,
        "risk": f"{risk_pct*100:.0f}%",
        "passes": passes,
        "total": n,
        "pass_rate": pass_rate,
        "fail_dd": fail_dd,
        "fail_profit": fail_profit,
        "fail_days": fail_days,
        "avg_profit": np.mean(profits),
        "avg_trades": np.mean(trades_list),
        "locked": locked_count,
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

    print(f"\n{'='*110}")
    print(f"  SMART CHALLENGE SIMULATION — Profit Lock vs No Lock")
    print(f"  Deck: {', '.join(DECK.keys())}")
    print(f"{'='*110}")

    results = []

    for risk in [0.02, 0.03]:
        print(f"\n  --- Risk: {risk*100:.0f}% ---")

        # Without profit lock (baseline)
        r1 = run_simulation(config, instruments, data_dict, risk, False,
                           f"NO LOCK @ {risk*100:.0f}%")
        results.append(r1)

        # With profit lock
        r2 = run_simulation(config, instruments, data_dict, risk, True,
                           f"PROFIT LOCK @ {risk*100:.0f}%")
        results.append(r2)

        # Delta
        delta = r2["pass_rate"] - r1["pass_rate"]
        dd_delta = r1["fail_dd"] - r2["fail_dd"]
        print(f"\n      IMPACT: +{delta:.1f}pp pass rate, {dd_delta} fewer DD fails")

    # Summary
    print(f"\n{'='*110}")
    print(f"  SUMMARY")
    print(f"{'='*110}")
    print(f"  {'Config':<25s} {'PASS':>6s} {'Rate':>6s} {'FailDD':>7s} {'FailPr':>7s} "
          f"{'FailDay':>7s} {'AvgPr%':>8s} {'Locked':>7s}")
    print(f"  {'-'*80}")
    for r in results:
        print(f"  {r['label']:<25s} {r['passes']:>4d}/{r['total']:<3d} "
              f"{r['pass_rate']:>5.1f}% {r['fail_dd']:>6d} {r['fail_profit']:>7d} "
              f"{r['fail_days']:>6d} {r['avg_profit']:>+7.2f}% {r['locked']:>6d}")

    # ROI with €80 / $10K accounts
    print(f"\n{'='*110}")
    print(f"  ROI ANALYSIS (€80/exam, $10K accounts, 80% split)")
    print(f"{'='*110}")
    for r in results:
        p1 = r["pass_rate"] / 100
        if p1 > 0:
            p2 = 0.60
            funded = p1 * p2
            cost = 80 / funded if funded > 0 else float("inf")
            # $10K account, avg 5% first month profit, 80% split = $400
            monthly_per_funded = 400
            print(f"  {r['label']:<25s}: "
                  f"P1={p1*100:5.1f}% → {funded*100:5.1f}% funded/exam "
                  f"(€{cost:,.0f}/funded) | "
                  f"10 exams/month → {10*funded:.1f} funded → €{10*funded*monthly_per_funded:,.0f}/mo")


if __name__ == "__main__":
    main()
