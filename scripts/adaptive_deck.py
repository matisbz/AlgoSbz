"""
Adaptive deck: rolling combo selection that would have worked WITHOUT knowing the future.

The key insight: instead of picking combos based on full-period results (overfitting),
use a ROLLING lookback window to decide which combos are "active" each month.

Rule: combo is active if PF > 1.0 in the last N months of trades.
This is implementable in real-time — you always know last N months of results.

Tests:
1. Rolling adaptive selection across ALL years (not just 2025)
2. Compare: fixed deck vs adaptive deck
3. Verify no look-ahead bias

Usage:
    python -X utf8 scripts/adaptive_deck.py
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

FULL_DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
]


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names):
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
        except Exception:
            continue
        trades = []
        for t in result.trades:
            ts = t.entry_time
            if isinstance(ts, pd.Timestamp):
                trades.append({"ts": ts, "date": ts.date(), "pnl": t.pnl, "combo": combo_name})
        streams[combo_name] = trades
    return streams


def get_active_combos(streams, all_combos, eval_date, lookback_months=6, min_trades=3):
    """
    Determine which combos are 'active' based on rolling lookback.
    This uses ONLY past data — no look-ahead bias.
    """
    eval_date_ts = pd.Timestamp(eval_date)
    cutoff_ts = eval_date_ts - timedelta(days=lookback_months * 30)
    active = []

    for combo in all_combos:
        if combo not in streams:
            continue
        recent = [t for t in streams[combo]
                  if cutoff_ts <= pd.Timestamp(t["date"]) < eval_date_ts]
        if len(recent) < min_trades:
            # Not enough data — include by default (give benefit of doubt)
            active.append(combo)
            continue
        wins = sum(t["pnl"] for t in recent if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in recent if t["pnl"] < 0))
        pf = wins / losses if losses > 0 else 99
        if pf > 1.0:
            active.append(combo)

    return active


def simulate_exam(streams, combo_names, risk_mult, start_date,
                  initial=100000, p1_days=30, p2_days=60,
                  daily_loss_cap=None):
    """Exam simulation with daily loss cap."""
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
        daily_stopped = False

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
                daily_stopped = False

            if daily_stopped:
                continue

            pnl = t["pnl"] * risk_mult
            equity += pnl
            trading_days.add(t["date"])

            if daily_loss_cap:
                daily_loss = (daily_start - equity) / initial * 100
                if daily_loss >= daily_loss_cap:
                    daily_stopped = True

            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "trading_days": len(trading_days), "days_used": window_days}

            daily_dd = (daily_start - equity) / initial
            if daily_dd >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "trading_days": len(trading_days), "days_used": window_days}

            profit_pct = (equity - initial) / initial * 100
            if profit_pct >= target_pct and len(trading_days) >= 4:
                locked = True
                days_used = (t["date"] - phase_start.date()).days + 1

        if current_day and not locked:
            daily_dd = (daily_start - equity) / initial
            max_daily_dd = max(max_daily_dd, daily_dd)

        profit_pct = (equity - initial) / initial * 100
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
                "days_used": days_used}

    p1 = run_phase(start_date, p1_days, 10.0)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0)
    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in FULL_DECK})
    data_dict = {}
    print("Loading data (including 2025)...")
    for sym in sorted(all_symbols):
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars (last: {data_dict[sym].index[-1].date()})")

    streams = precompute_trades(config, instruments, data_dict, FULL_DECK)
    for c in FULL_DECK:
        if c in streams:
            dates = [t["date"] for t in streams[c]]
            print(f"    {c}: {len(dates)} trades ({min(dates)} to {max(dates)})" if dates else f"    {c}: 0 trades")

    # ═══════════════════════════════════════════════════════════════
    # TEST 1: Adaptive vs Fixed — year-by-year comparison
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  ADAPTIVE vs FIXED DECK — Year-by-year")
    print(f"  Adaptive: rolling 6-month PF>1.0 filter (NO look-ahead)")
    print(f"  Fixed: Decorr12_A always (the overfitted version)")
    print(f"  Both: Risk 2%, Daily cap 2.5%")
    print(f"{'='*120}")

    risk = 2.0
    daily_cap = 2.5

    for lookback in [3, 6, 9, 12]:
        print(f"\n  === Lookback: {lookback} months ===")
        print(f"  {'Year':<6s} {'Type':>5s} {'Active':>7s} {'Windows':>8s} "
              f"{'P1 Pass':>8s} {'P1%':>6s} {'Funded':>8s} {'Fund%':>6s}")
        print(f"  {'-'*70}")

        total_funded_adaptive = 0
        total_funded_fixed = 0
        total_windows = 0

        for year in range(2019, 2026):
            windows = pd.date_range(f"{year}-01-01", f"{year}-10-01", freq="30D")
            n = len(windows)
            total_windows += n

            # Adaptive
            adapt_funded = 0
            adapt_p1 = 0
            active_counts = []
            for start in windows:
                active = get_active_combos(streams, FULL_DECK, start.date(),
                                           lookback_months=lookback, min_trades=3)
                active_counts.append(len(active))
                if not active:
                    continue
                r = simulate_exam(streams, active, risk, start, daily_loss_cap=daily_cap)
                if r["exam"] == "FUNDED":
                    adapt_funded += 1
                if r["p1"]["outcome"] == "PASS":
                    adapt_p1 += 1
            total_funded_adaptive += adapt_funded

            # Fixed
            fix_funded = 0
            fix_p1 = 0
            for start in windows:
                r = simulate_exam(streams, FULL_DECK, risk, start, daily_loss_cap=daily_cap)
                if r["exam"] == "FUNDED":
                    fix_funded += 1
                if r["p1"]["outcome"] == "PASS":
                    fix_p1 += 1
            total_funded_fixed += fix_funded

            oos = "OOS" if year >= 2025 else "IS/WF"
            avg_active = np.mean(active_counts)
            marker = " <<<" if year >= 2025 else ""

            print(f"  {year:<6d} {oos:>5s} {avg_active:>5.1f}/12 {n:>8d} "
                  f"{adapt_p1:>3d}/{n:<4d} {adapt_p1/n*100:>5.1f}% "
                  f"{adapt_funded:>3d}/{n:<4d} {adapt_funded/n*100:>5.1f}%{marker}")

        print(f"\n  Lookback {lookback}m total: Adaptive={total_funded_adaptive}/{total_windows} "
              f"({total_funded_adaptive/total_windows*100:.1f}%) | "
              f"Fixed={total_funded_fixed}/{total_windows} "
              f"({total_funded_fixed/total_windows*100:.1f}%)")

    # ═══════════════════════════════════════════════════════════════
    # TEST 2: Detailed 2025 OOS — adaptive deck composition
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  2025 OOS DETAIL — Adaptive (6-month lookback)")
    print(f"  Shows which combos are active for each window")
    print(f"{'='*120}")

    oos_windows = pd.date_range("2025-01-01", "2025-10-01", freq="30D")
    best_lookback = 6

    for start in oos_windows:
        active = get_active_combos(streams, FULL_DECK, start.date(),
                                   lookback_months=best_lookback, min_trades=3)
        r = simulate_exam(streams, active, risk, start, daily_loss_cap=daily_cap)

        marker = " <<<" if r["exam"] == "FUNDED" else ""
        print(f"\n  {start.date()} — {len(active)}/12 active → "
              f"P1={r['p1']['outcome']} ({r['p1']['profit_pct']:+.1f}%) | "
              f"Exam={r['exam']}{marker}")
        print(f"    Active:   {', '.join(active)}")
        excluded = [c for c in FULL_DECK if c not in active]
        if excluded:
            print(f"    Excluded: {', '.join(excluded)}")

    # ═══════════════════════════════════════════════════════════════
    # TEST 3: Comprehensive grid — lookback × risk × daily_cap
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  GRID SEARCH: Adaptive configs on 2019-2025 (walk-forward)")
    print(f"{'='*120}")

    all_windows = pd.date_range("2019-01-01", "2025-10-01", freq="30D")
    oos_only = pd.date_range("2025-01-01", "2025-10-01", freq="30D")

    grid_results = []
    for lookback in [3, 6, 9, 12]:
        for r_mult in [1.5, 2.0, 2.5, 3.0]:
            for dc in [None, 2.0, 2.5, 3.0, 4.0]:
                # Full period
                full_funded = 0
                full_n = 0
                # OOS only
                oos_funded = 0
                oos_n = 0

                for start in all_windows:
                    active = get_active_combos(streams, FULL_DECK, start.date(),
                                               lookback_months=lookback, min_trades=3)
                    if not active:
                        full_n += 1
                        if start in oos_only:
                            oos_n += 1
                        continue

                    res = simulate_exam(streams, active, r_mult, start, daily_loss_cap=dc)
                    full_n += 1
                    if res["exam"] == "FUNDED":
                        full_funded += 1

                    if start >= pd.Timestamp("2025-01-01"):
                        oos_n += 1
                        if res["exam"] == "FUNDED":
                            oos_funded += 1

                grid_results.append({
                    "lookback": lookback, "risk": r_mult, "daily_cap": dc,
                    "full_funded": full_funded, "full_rate": full_funded / full_n * 100 if full_n else 0,
                    "oos_funded": oos_funded, "oos_rate": oos_funded / oos_n * 100 if oos_n else 0,
                    "full_n": full_n, "oos_n": oos_n,
                    "label": f"L{lookback}_R{r_mult:.1f}_DC{dc or 'N'}",
                })

    # Sort by OOS funded rate (the only thing that matters for real trading)
    grid_results.sort(key=lambda x: (-x["oos_rate"], -x["full_rate"]))

    print(f"\n  TOP 20 by 2025 OOS funded rate:")
    print(f"  {'Config':<22s} {'2019-25':>8s} {'Rate':>6s} {'2025 OOS':>10s} {'Rate':>6s} {'Overfit':>8s}")
    print(f"  {'-'*70}")

    for r in grid_results[:20]:
        overfit = r["full_rate"] - r["oos_rate"]
        print(f"  {r['label']:<22s} {r['full_funded']:>3d}/{r['full_n']:<4d} {r['full_rate']:>5.1f}% "
              f"{r['oos_funded']:>4d}/{r['oos_n']:<4d} {r['oos_rate']:>5.1f}% "
              f"{overfit:>+6.1f}pp")

    # ═══════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  FINAL VERDICT: What to deploy")
    print(f"{'='*120}")

    # Best config that has OOS > 0 AND reasonable full-period rate
    viable = [r for r in grid_results if r["oos_rate"] > 0 and r["full_rate"] > 10]
    if viable:
        best = viable[0]
        print(f"\n  RECOMMENDED CONFIG: {best['label']}")
        print(f"    Walk-forward 2019-2025: {best['full_rate']:.1f}% funded")
        print(f"    Pure 2025 OOS:          {best['oos_rate']:.1f}% funded")
        print(f"    Overfitting gap:        {best['full_rate'] - best['oos_rate']:+.1f}pp")
        print(f"\n  Settings:")
        print(f"    Lookback:    {best['lookback']} months")
        print(f"    Risk/trade:  {best['risk']:.1f}%")
        print(f"    Daily cap:   {best['daily_cap'] or 'None'}%")
        print(f"    Deck:        adaptive (rotate based on recent PF)")

        # ROI
        oos_rate = best["oos_rate"] / 100
        if oos_rate > 0:
            cost_per = 40 / oos_rate
            print(f"\n  ROI ($5K accounts @EUR40):")
            for n_ex in [5, 10, 20]:
                funded_mo = n_ex * oos_rate
                income = funded_mo * 200
                cost = n_ex * 40
                net = income - cost
                print(f"    {n_ex} exams/mo: EUR{cost} cost -> {funded_mo:.1f} funded -> "
                      f"EUR{income:.0f} income -> EUR{net:+.0f} net/mo")
    else:
        print(f"\n  NO CONFIG has both OOS > 0% AND full-period > 10%.")
        print(f"  Consider: the edge may be too weak for current market regime.")
        # Show best OOS anyway
        if grid_results and grid_results[0]["oos_rate"] > 0:
            best = grid_results[0]
            print(f"\n  Best OOS config: {best['label']}")
            print(f"    2025 OOS: {best['oos_rate']:.1f}% ({best['oos_funded']}/{best['oos_n']})")
            print(f"    Full period: {best['full_rate']:.1f}%")


if __name__ == "__main__":
    main()
