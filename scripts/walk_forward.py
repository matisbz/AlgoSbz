"""
Walk-forward validation + 2025 out-of-sample test.

1. Year-by-year breakdown: funded rate per year (detect edge decay)
2. Pure OOS 2025: exam windows on data never seen during development
3. Rolling walk-forward: train on N years, test on next year

Usage:
    python -X utf8 scripts/walk_forward.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
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

from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY

# The winning deck from optimize_deck.py
BEST_DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
]
BEST_RISK = 3.0  # multiplier on 1% base

# Also test Core3 for comparison
CORE3 = ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1"]
CORE3_RISK = 3.0


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names):
    """Pre-compute trades at 1% risk for given combos."""
    streams = {}
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.01
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)], daily_stop_threshold=0.048,
        progressive_trades=0, consecutive_win_bonus=0,
    )

    for combo_name in combo_names:
        entry = ALL_COMBOS[combo_name]
        sym = entry["symbol"]
        if sym not in data_dict:
            continue
        try:
            strategy = load_strategy(entry)
            engine = BacktestEngine(cfg, instruments[sym], EquityManager(eq_cfg))
            result = engine.run(strategy, data_dict[sym], sym)
        except Exception as e:
            print(f"    {combo_name}: ERROR {e}")
            continue

        trades = []
        for t in result.trades:
            ts = t.entry_time
            if isinstance(ts, pd.Timestamp):
                trades.append({"ts": ts, "date": ts.date(), "pnl": t.pnl})
        streams[combo_name] = trades

    return streams


def simulate_exam(streams, combo_names, risk_mult, start_date,
                  initial=100000, p1_days=30, p2_days=60):
    """Fast exam simulation using pre-computed trade streams."""
    def run_phase(phase_start, window_days, target_pct):
        phase_end = phase_start + timedelta(days=window_days)
        equity = initial
        daily_start = initial
        trading_days = set()
        current_day = None
        max_dd = 0
        max_daily_dd = 0
        locked = False
        days_used = window_days

        all_trades = []
        for combo in combo_names:
            if combo not in streams:
                continue
            for t in streams[combo]:
                if phase_start.date() <= t["date"] < phase_end.date():
                    all_trades.append(t)
        all_trades.sort(key=lambda x: x["ts"])

        for t in all_trades:
            if locked:
                break

            if t["date"] != current_day:
                if current_day is not None:
                    daily_dd = (daily_start - equity) / initial
                    max_daily_dd = max(max_daily_dd, daily_dd)
                current_day = t["date"]
                daily_start = equity

            pnl = t["pnl"] * risk_mult
            equity += pnl
            trading_days.add(t["date"])

            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "trading_days": len(trading_days), "days_used": window_days,
                        "trades": len([x for x in all_trades if x["ts"] <= t["ts"]])}

            daily_dd = (daily_start - equity) / initial
            if daily_dd >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "trading_days": len(trading_days), "days_used": window_days,
                        "trades": len([x for x in all_trades if x["ts"] <= t["ts"]])}

            profit_pct = (equity - initial) / initial * 100
            if profit_pct >= target_pct and len(trading_days) >= 4:
                locked = True
                days_used = (t["date"] - phase_start.date()).days + 1

        if current_day and not locked:
            daily_dd = (daily_start - equity) / initial
            max_daily_dd = max(max_daily_dd, daily_dd)

        profit_pct = (equity - initial) / initial * 100
        n_trades = len(all_trades) if not locked else len([x for x in all_trades if not locked or x["ts"] <= t["ts"]])

        if max_dd >= 0.10:
            outcome = "FAIL_DD"
        elif max_daily_dd >= 0.05:
            outcome = "FAIL_DAILY_DD"
        elif profit_pct >= target_pct and len(trading_days) >= 4:
            outcome = "PASS"
        elif profit_pct >= target_pct:
            outcome = "FAIL_MIN_DAYS"
        else:
            outcome = "FAIL_PROFIT"

        return {"outcome": outcome, "profit_pct": round(profit_pct, 2),
                "max_dd": round(max_dd * 100, 2), "trading_days": len(trading_days),
                "days_used": days_used, "trades": len(all_trades)}

    p1 = run_phase(start_date, p1_days, 10.0)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def eval_windows(streams, combo_names, risk_mult, window_starts):
    """Evaluate exam windows and return detailed results."""
    results = []
    for start in window_starts:
        r = simulate_exam(streams, combo_names, risk_mult, start)
        results.append({"start": start, **r})
    return results


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Load data INCLUDING 2025
    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in BEST_DECK + CORE3})
    data_dict = {}
    print("Loading data (including 2025)...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
            last_date = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (last: {last_date.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Pre-compute trade streams
    all_combos = list(set(BEST_DECK + CORE3))
    print(f"\n  Pre-computing trades for {len(all_combos)} combos...")
    streams = precompute_trades(config, instruments, data_dict, all_combos)
    for c in all_combos:
        if c in streams:
            n = len(streams[c])
            dates = [t["date"] for t in streams[c]]
            if dates:
                print(f"    {c}: {n} trades ({min(dates)} to {max(dates)})")

    # ═══════════════════════════════════════════════════════════════
    # TEST 1: Year-by-year breakdown (detect edge decay)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  TEST 1: YEAR-BY-YEAR FUNDED RATE")
    print(f"  In-sample: 2015-2024 | Out-of-sample: 2025")
    print(f"{'='*120}")

    for deck_name, combo_names, risk_mult in [
        ("Decorr12_A", BEST_DECK, BEST_RISK),
        ("Core3", CORE3, CORE3_RISK),
    ]:
        print(f"\n  --- {deck_name} @{risk_mult:.0f}% ---")
        print(f"  {'Year':<6s} {'Windows':>8s} {'P1 Pass':>8s} {'P1%':>6s} "
              f"{'Funded':>8s} {'Fund%':>6s} {'DD Fail':>8s} {'AvgProf':>8s} {'Type':>5s}")
        print(f"  {'-'*80}")

        for year in range(2015, 2026):
            # Exam windows every 30 days within this year
            year_start = pd.Timestamp(f"{year}-01-01")
            year_end = pd.Timestamp(f"{year}-12-01")
            windows = pd.date_range(year_start, year_end, freq="30D")

            if len(windows) == 0:
                continue

            results = eval_windows(streams, combo_names, risk_mult, windows)

            funded = sum(1 for r in results if r["exam"] == "FUNDED")
            p1_pass = sum(1 for r in results if r["p1"]["outcome"] == "PASS")
            dd_fails = sum(1 for r in results if "DD" in r["p1"]["outcome"])
            avg_profit = np.mean([r["p1"]["profit_pct"] for r in results])
            n = len(results)

            oos = "OOS" if year >= 2025 else "IS"
            marker = " <<<" if year >= 2025 else ""

            print(f"  {year:<6d} {n:>8d} {p1_pass:>3d}/{n:<4d} {p1_pass/n*100:>5.1f}% "
                  f"{funded:>3d}/{n:<4d} {funded/n*100:>5.1f}% "
                  f"{dd_fails:>8d} {avg_profit:>+7.2f}% {oos:>5s}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # TEST 2: Pure 2025 out-of-sample — detailed windows
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  TEST 2: 2025 OUT-OF-SAMPLE — Detailed exam breakdown")
    print(f"  These strategies NEVER saw 2025 data during development")
    print(f"{'='*120}")

    oos_windows = pd.date_range("2025-01-01", "2025-10-01", freq="30D")

    for deck_name, combo_names, risk_mult in [
        ("Decorr12_A", BEST_DECK, BEST_RISK),
        ("Core3", CORE3, CORE3_RISK),
    ]:
        print(f"\n  --- {deck_name} @{risk_mult:.0f}% ---")
        print(f"  {'Window':<12s} {'P1':>10s} {'P1 Profit':>10s} {'P1 DD':>6s} {'P1 Days':>7s} "
              f"{'P2':>10s} {'P2 Profit':>10s} {'P2 DD':>6s} {'Exam':>10s}")
        print(f"  {'-'*95}")

        results = eval_windows(streams, combo_names, risk_mult, oos_windows)
        funded = 0
        p1_pass = 0

        for r in results:
            p1 = r["p1"]
            p2_out = r["p2"]["outcome"] if r["p2"] else "-"
            p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
            p2_dd = f"{r['p2']['max_dd']:.1f}%" if r["p2"] else "-"
            marker = " <<<" if r["exam"] == "FUNDED" else ""

            print(f"  {str(r['start'].date()):<12s} {p1['outcome']:>10s} {p1['profit_pct']:>+9.1f}% "
                  f"{p1['max_dd']:>5.1f}% {p1['trading_days']:>4d}d   "
                  f"{p2_out:>10s} {p2_prof:>10s} {p2_dd:>6s} {r['exam']:>10s}{marker}")

            if r["exam"] == "FUNDED":
                funded += 1
            if p1["outcome"] == "PASS":
                p1_pass += 1

        n = len(results)
        print(f"\n  2025 OOS: P1={p1_pass}/{n} ({p1_pass/n*100:.1f}%) | "
              f"FUNDED={funded}/{n} ({funded/n*100:.1f}%)")

    # ═══════════════════════════════════════════════════════════════
    # TEST 3: Rolling walk-forward (train/test split)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  TEST 3: ROLLING WALK-FORWARD")
    print(f"  For each test year: only use trades from combos with PF>1.0 in prior 4 years")
    print(f"  This validates the combo selection process itself")
    print(f"{'='*120}")

    for deck_name, combo_names, risk_mult in [
        ("Decorr12_A", BEST_DECK, BEST_RISK),
    ]:
        print(f"\n  --- {deck_name} @{risk_mult:.0f}% ---")
        print(f"  {'Test Year':<10s} {'Combos OK':>10s} {'Windows':>8s} {'P1 Pass':>8s} "
              f"{'Funded':>8s} {'Fund%':>6s} {'Type':>5s}")
        print(f"  {'-'*70}")

        for test_year in range(2019, 2026):
            train_start = pd.Timestamp(f"{test_year-4}-01-01")
            train_end = pd.Timestamp(f"{test_year}-01-01")

            # Filter combos: only those with PF > 1.0 in training period
            valid_combos = []
            for combo in combo_names:
                if combo not in streams:
                    continue
                train_trades = [t for t in streams[combo]
                                if train_start.date() <= t["date"] < train_end.date()]
                if len(train_trades) < 5:
                    continue
                wins = sum(t["pnl"] for t in train_trades if t["pnl"] > 0)
                losses = abs(sum(t["pnl"] for t in train_trades if t["pnl"] < 0))
                pf = wins / losses if losses > 0 else 999
                if pf > 1.0:
                    valid_combos.append(combo)

            # Test on the test year
            test_windows = pd.date_range(f"{test_year}-01-01", f"{test_year}-10-01", freq="30D")
            results = eval_windows(streams, valid_combos, risk_mult, test_windows)

            funded = sum(1 for r in results if r["exam"] == "FUNDED")
            p1_pass = sum(1 for r in results if r["p1"]["outcome"] == "PASS")
            n = len(results)

            oos = "OOS" if test_year >= 2025 else "WF"
            marker = " <<<" if test_year >= 2025 else ""

            print(f"  {test_year:<10d} {len(valid_combos):>5d}/{len(combo_names):<4d} "
                  f"{n:>8d} {p1_pass:>3d}/{n:<4d} "
                  f"{funded:>3d}/{n:<4d} {funded/n*100:>5.1f}% {oos:>5s}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  VERDICT: Is the edge real?")
    print(f"{'='*120}")

    # Compute IS vs OOS for Decorr12_A
    is_windows = pd.date_range("2015-01-01", "2024-06-01", freq="90D")
    oos_windows_full = pd.date_range("2025-01-01", "2025-10-01", freq="30D")

    is_results = eval_windows(streams, BEST_DECK, BEST_RISK, is_windows)
    oos_results = eval_windows(streams, BEST_DECK, BEST_RISK, oos_windows_full)

    is_funded = sum(1 for r in is_results if r["exam"] == "FUNDED") / len(is_results) * 100
    oos_funded = sum(1 for r in oos_results if r["exam"] == "FUNDED") / len(oos_results) * 100

    is_p1 = sum(1 for r in is_results if r["p1"]["outcome"] == "PASS") / len(is_results) * 100
    oos_p1 = sum(1 for r in oos_results if r["p1"]["outcome"] == "PASS") / len(oos_results) * 100

    print(f"\n  Decorr12_A @3% risk:")
    print(f"    In-sample  (2015-2024, {len(is_results)} windows): P1={is_p1:.1f}% | Funded={is_funded:.1f}%")
    print(f"    Out-of-sample (2025, {len(oos_results)} windows):  P1={oos_p1:.1f}% | Funded={oos_funded:.1f}%")

    degradation = (is_funded - oos_funded) / is_funded * 100 if is_funded > 0 else 0
    if oos_funded >= is_funded * 0.7:
        verdict = "EDGE CONFIRMED — OOS degradation < 30%"
    elif oos_funded >= is_funded * 0.5:
        verdict = "EDGE PARTIAL — significant OOS degradation, use with caution"
    elif oos_funded > 0:
        verdict = "EDGE WEAK — high OOS degradation, review strategy selection"
    else:
        verdict = "NO EDGE — OOS funded rate is 0%, do NOT go live"

    print(f"    Degradation: {degradation:.1f}%")
    print(f"    VERDICT: {verdict}")

    # ROI for $5K accounts
    print(f"\n  ROI for $5K accounts @EUR40/exam (using OOS rate):")
    if oos_funded > 0:
        cost_per = 40 / (oos_funded / 100)
        for n_exams in [5, 10, 20]:
            funded_mo = n_exams * oos_funded / 100
            income = funded_mo * 200  # ~$200/mo per $5K funded
            cost = n_exams * 40
            print(f"    {n_exams} exams/mo: EUR{cost} cost -> {funded_mo:.1f} funded -> "
                  f"EUR{income:.0f}/mo income (EUR{cost_per:.0f}/funded)")
    else:
        print(f"    DO NOT INVEST — no edge confirmed in OOS data")


if __name__ == "__main__":
    main()
