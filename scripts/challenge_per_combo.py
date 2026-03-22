"""
Per-combo FTMO challenge pass rate.

Tests each combo INDIVIDUALLY to see if the "1 combo per exam" model works.
Also tests the full Core3 deck for comparison.

Usage:
    python -X utf8 scripts/challenge_per_combo.py
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
import importlib

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

COMBOS = {
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
    "MomDiv_SPY_H1": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "symbol": "SPY",
        "params": {"timeframe": "H1"},
    },
}


def run_phase(config, instruments, data_dict, combo_names, risk_pct,
              start_date, window_days, profit_target_pct):
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

    for combo_name in combo_names:
        entry = COMBOS[combo_name]
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
            mod = importlib.import_module(entry["module"])
            cls = getattr(mod, entry["class"])
            strategy = cls(entry["params"])
            engine = BacktestEngine(cfg, inst_cfg, EquityManager(eq_cfg))
            result = engine.run(strategy, window_data, sym)
        except Exception:
            continue

        for t in result.trades:
            t_ts = t.entry_time
            t_date = t_ts.date() if hasattr(t_ts, 'date') else t_ts
            if isinstance(t_date, pd.Timestamp):
                t_date = t_date.date()
            if start_date.date() <= t_date < end_date.date():
                all_trades.append({"ts": t_ts, "date": t_date, "pnl": t.pnl})

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                all_equity_points.append((ts, val - initial))

    if not all_equity_points:
        return {"outcome": "NO_DATA", "profit_pct": 0, "trades": 0,
                "trading_days": 0, "days_used": window_days}

    all_trades.sort(key=lambda x: x["ts"])

    df = pd.DataFrame(all_equity_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    # Profit lock
    lock_ts = None
    trading_days_so_far = set()
    for t in all_trades:
        trading_days_so_far.add(t["date"])
        trade_ts = t["ts"]
        if isinstance(trade_ts, pd.Timestamp):
            eq_before = combined_equity[combined_equity.index <= trade_ts]
            if not eq_before.empty:
                profit_now = (eq_before.iloc[-1] - initial) / initial * 100
                if profit_now >= profit_target_pct and len(trading_days_so_far) >= 4:
                    lock_ts = trade_ts
                    break

    if lock_ts:
        eq = combined_equity[combined_equity.index <= lock_ts]
        trades_counted = [t for t in all_trades if t["ts"] <= lock_ts]
        days_used = (lock_ts - pd.Timestamp(start_date)).days + 1
    else:
        eq = combined_equity
        trades_counted = all_trades
        days_used = window_days

    trading_days = set(t["date"] for t in trades_counted)
    profit_pct = (eq.iloc[-1] - initial) / initial * 100

    max_dd = max((initial - val) / initial for val in eq)

    max_daily_dd = 0
    daily = eq.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily.iterrows():
        if row["first"] > 0:
            ddd = (row["first"] - row["min"]) / row["first"]
            max_daily_dd = max(max_daily_dd, ddd)

    if max_dd >= 0.10:
        outcome = "FAIL_DD"
    elif max_daily_dd >= 0.05:
        outcome = "FAIL_DAILY_DD"
    elif profit_pct >= profit_target_pct and len(trading_days) >= 4:
        outcome = "PASS"
    elif profit_pct >= profit_target_pct:
        outcome = "FAIL_MIN_DAYS"
    else:
        outcome = "FAIL_PROFIT"

    return {"outcome": outcome, "profit_pct": round(profit_pct, 2),
            "trades": len(trades_counted), "trading_days": len(trading_days),
            "days_used": days_used}


def run_exam(config, instruments, data_dict, combo_names, risk_pct, start_date):
    p1 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   start_date, 30, 10.0)
    if p1["outcome"] != "PASS":
        return "FAIL_P1", p1, None

    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   p2_start, 60, 5.0)
    if p2["outcome"] == "PASS":
        return "FUNDED", p1, p2
    return "FAIL_P2", p1, p2


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    data_dict = {}
    print("Loading data...")
    for sym in ["USDCHF", "XTIUSD", "SPY"]:
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    window_starts = pd.date_range("2015-01-01", "2024-06-01", freq="90D")
    n = len(window_starts)
    print(f"\n  Exam windows: {n}")

    risk = 0.03
    print(f"\n{'='*100}")
    print(f"  PER-COMBO PASS RATE vs FULL DECK — Risk: {risk*100:.0f}%")
    print(f"{'='*100}")

    configs = {
        "Core3 (full deck)": list(COMBOS.keys()),
        "VMR_USDCHF_H1 solo": ["VMR_USDCHF_H1"],
        "SwBrk_XTIUSD_H4 solo": ["SwBrk_XTIUSD_H4"],
        "MomDiv_SPY_H1 solo": ["MomDiv_SPY_H1"],
    }

    results = {}
    for label, combos in configs.items():
        funded = 0
        p1_pass = 0
        p1_fails = defaultdict(int)
        outcomes_list = []

        for start in window_starts:
            outcome, p1, p2 = run_exam(config, instruments, data_dict, combos,
                                        risk, start)
            if p1["outcome"] == "PASS":
                p1_pass += 1
            else:
                p1_fails[p1["outcome"]] += 1
            if outcome == "FUNDED":
                funded += 1
            outcomes_list.append(outcome)

        p1_rate = p1_pass / n * 100
        funded_rate = funded / n * 100
        p2_rate = funded / p1_pass * 100 if p1_pass > 0 else 0

        print(f"\n  {label}:")
        print(f"    P1: {p1_pass}/{n} ({p1_rate:.1f}%) | "
              f"P2: {funded}/{p1_pass if p1_pass else '?'} ({p2_rate:.1f}%) | "
              f"FUNDED: {funded}/{n} ({funded_rate:.1f}%)")
        print(f"    P1 fails: DD={p1_fails.get('FAIL_DD',0)+p1_fails.get('FAIL_DAILY_DD',0)} "
              f"Profit={p1_fails.get('FAIL_PROFIT',0)} "
              f"Days={p1_fails.get('FAIL_MIN_DAYS',0)}")

        results[label] = {
            "funded_rate": funded_rate,
            "p1_rate": p1_rate,
            "outcomes": outcomes_list,
        }

    # Correlation analysis
    print(f"\n{'='*100}")
    print(f"  CORRELACIÓN ENTRE COMBOS (misma fecha de inicio)")
    print(f"{'='*100}")

    combo_labels = ["VMR_USDCHF_H1 solo", "SwBrk_XTIUSD_H4 solo", "MomDiv_SPY_H1 solo"]
    for i, l1 in enumerate(combo_labels):
        for l2 in combo_labels[i+1:]:
            o1 = results[l1]["outcomes"]
            o2 = results[l2]["outcomes"]
            both_funded = sum(1 for a, b in zip(o1, o2) if a == "FUNDED" and b == "FUNDED")
            both_fail = sum(1 for a, b in zip(o1, o2) if a != "FUNDED" and b != "FUNDED")
            same = both_funded + both_fail
            diff = n - same
            corr = same / n * 100
            print(f"\n  {l1.replace(' solo','')} vs {l2.replace(' solo','')}:")
            print(f"    Both funded: {both_funded} | Both fail: {both_fail} | "
                  f"Different: {diff} | Same outcome: {corr:.0f}%")

    # Model D simulation
    print(f"\n{'='*100}")
    print(f"  MODELO D: 3 combos × 3 fechas = 9 exámenes/mes")
    print(f"{'='*100}")

    # For each 90-day period, simulate buying 9 exams (3 combos × 3 dates)
    offsets = [0, 10, 20]  # days offset
    total_funded = 0
    total_exams = 0

    for start in window_starts:
        period_funded = 0
        for offset in offsets:
            exam_start = start + timedelta(days=offset)
            for combo_name in COMBOS:
                outcome, _, _ = run_exam(config, instruments, data_dict,
                                          [combo_name], risk, exam_start)
                total_exams += 1
                if outcome == "FUNDED":
                    period_funded += 1
                    total_funded += 1

    effective_rate = total_funded / total_exams * 100 if total_exams > 0 else 0
    print(f"\n  Total exams: {total_exams}")
    print(f"  Total funded: {total_funded}")
    print(f"  Funded rate per exam: {effective_rate:.1f}%")
    print(f"  Avg funded per 9-exam batch: {total_funded/len(window_starts):.2f}")
    print(f"\n  Monthly cost: 9 × €80 = €720")
    print(f"  Monthly funded (expected): {9 * effective_rate / 100:.2f}")
    print(f"  Monthly income per funded: ~€200")


if __name__ == "__main__":
    main()
