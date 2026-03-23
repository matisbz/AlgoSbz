"""
Diagnose why 2024-2025 fails and test fixes.

1. Which trades/combos cause daily DD blowups?
2. Has ATR changed (volatility regime shift)?
3. Test fixes: daily risk cap, lower risk, vol-adaptive sizing

Usage:
    python -X utf8 scripts/diagnose_2025.py
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
from algosbz.data.indicators import atr

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY

BEST_DECK = [
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
                trades.append({
                    "ts": ts, "date": ts.date(), "pnl": t.pnl,
                    "combo": combo_name, "symbol": entry["symbol"],
                })
        streams[combo_name] = trades
    return streams


def simulate_exam_v2(streams, combo_names, risk_mult, start_date,
                     initial=100000, p1_days=30, p2_days=60,
                     daily_loss_cap=None, max_trades_per_day=None):
    """Enhanced exam sim with daily risk controls."""
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
        daily_trades_count = 0
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
                daily_trades_count = 0
                daily_stopped = False

            # Daily risk controls
            if daily_stopped:
                continue
            if max_trades_per_day and daily_trades_count >= max_trades_per_day:
                continue

            pnl = t["pnl"] * risk_mult
            equity += pnl
            trading_days.add(t["date"])
            daily_trades_count += 1

            # Daily loss cap: stop trading this day if daily loss exceeds cap
            if daily_loss_cap:
                daily_loss = (daily_start - equity) / initial * 100
                if daily_loss >= daily_loss_cap:
                    daily_stopped = True

            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": max_daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            daily_dd = (daily_start - equity) / initial
            if daily_dd >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

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
                "max_dd": round(max_dd * 100, 2), "max_daily_dd": round(max_daily_dd * 100, 2),
                "trading_days": len(trading_days), "days_used": days_used}

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

    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in BEST_DECK})
    data_dict = {}
    print("Loading data...")
    for sym in sorted(all_symbols):
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars (last: {data_dict[sym].index[-1].date()})")

    streams = precompute_trades(config, instruments, data_dict, BEST_DECK)

    # ═══════════════════════════════════════════════════════════════
    # DIAGNOSTIC 1: What causes daily DD fails in 2025?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  DIAGNOSTIC 1: Daily DD fail anatomy in 2024-2025")
    print(f"{'='*120}")

    risk_mult = 3.0
    for year in [2024, 2025]:
        print(f"\n  --- {year} ---")
        windows = pd.date_range(f"{year}-01-01", f"{year}-10-01", freq="30D")

        for start in windows:
            end = start + timedelta(days=30)
            # Collect all trades in window
            all_trades = []
            for combo in BEST_DECK:
                if combo not in streams:
                    continue
                for t in streams[combo]:
                    if start.date() <= t["date"] < end.date():
                        all_trades.append(t)
            all_trades.sort(key=lambda x: x["ts"])

            # Find worst day
            daily_pnl = defaultdict(list)
            for t in all_trades:
                daily_pnl[t["date"]].append(t)

            worst_day = None
            worst_loss = 0
            for day, trades in daily_pnl.items():
                day_loss = sum(t["pnl"] * risk_mult for t in trades)
                day_loss_pct = day_loss / 100000 * 100
                if day_loss_pct < worst_loss:
                    worst_loss = day_loss_pct
                    worst_day = day
                    worst_trades = trades

            if worst_day and worst_loss < -4.0:  # Near or over daily DD limit
                print(f"\n    Window {start.date()}: worst day = {worst_day} ({worst_loss:+.2f}%)")
                combo_losses = defaultdict(float)
                for t in worst_trades:
                    pnl_pct = t["pnl"] * risk_mult / 100000 * 100
                    combo_losses[t["combo"]] += pnl_pct
                for combo, loss in sorted(combo_losses.items(), key=lambda x: x[1]):
                    print(f"      {combo:35s} {loss:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    # DIAGNOSTIC 2: ATR evolution (volatility regime shift?)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  DIAGNOSTIC 2: ATR evolution by year (volatility regime)")
    print(f"{'='*120}")

    for sym in sorted(all_symbols):
        data = data_dict[sym]
        # Resample to H4
        h4 = data.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        atr_vals = atr(h4["high"], h4["low"], h4["close"], 14)

        print(f"\n  {sym}:")
        print(f"    {'Year':<6s} {'Mean ATR':>10s} {'Median':>10s} {'P90':>10s} {'vs 2020':>10s}")
        ref_atr = None
        for year in range(2018, 2026):
            mask = atr_vals.index.year == year
            if mask.sum() < 100:
                continue
            year_atr = atr_vals[mask]
            mean_atr = year_atr.mean()
            med_atr = year_atr.median()
            p90_atr = year_atr.quantile(0.90)
            if year == 2020:
                ref_atr = mean_atr
            ratio = mean_atr / ref_atr if ref_atr else 1.0
            marker = " <<<" if year >= 2024 else ""
            print(f"    {year:<6d} {mean_atr:>10.4f} {med_atr:>10.4f} {p90_atr:>10.4f} {ratio:>9.1f}x{marker}")

    # ═══════════════════════════════════════════════════════════════
    # DIAGNOSTIC 3: Per-combo PF in 2024-2025 vs historical
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  DIAGNOSTIC 3: Per-combo edge in recent years")
    print(f"{'='*120}")

    print(f"\n  {'Combo':<35s} {'2018-21 PF':>10s} {'2022-23 PF':>10s} {'2024 PF':>10s} {'2025 PF':>10s}")
    print(f"  {'-'*80}")

    for combo in BEST_DECK:
        if combo not in streams:
            continue
        trades = streams[combo]
        periods = {
            "2018-21": (2018, 2022),
            "2022-23": (2022, 2024),
            "2024": (2024, 2025),
            "2025": (2025, 2026),
        }
        pfs = {}
        for label, (y1, y2) in periods.items():
            period_trades = [t for t in trades
                             if pd.Timestamp(f"{y1}-01-01").date() <= t["date"] < pd.Timestamp(f"{y2}-01-01").date()]
            if len(period_trades) < 3:
                pfs[label] = "n/a"
                continue
            wins = sum(t["pnl"] for t in period_trades if t["pnl"] > 0)
            losses = abs(sum(t["pnl"] for t in period_trades if t["pnl"] < 0))
            pf = wins / losses if losses > 0 else 99
            pfs[label] = f"{pf:.2f}"

        print(f"  {combo:<35s} {pfs['2018-21']:>10s} {pfs['2022-23']:>10s} {pfs['2024']:>10s} {pfs['2025']:>10s}")

    # ═══════════════════════════════════════════════════════════════
    # FIX TESTS: Grid search over risk controls
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  FIX TESTS: Risk controls grid search")
    print(f"  Testing on 2023-2025 (recent regime)")
    print(f"{'='*120}")

    test_windows = pd.date_range("2023-01-01", "2025-10-01", freq="30D")

    configs = []
    # Grid: risk_mult × daily_loss_cap × max_trades_per_day
    for risk in [1.0, 1.5, 2.0, 2.5, 3.0]:
        for daily_cap in [None, 2.0, 2.5, 3.0, 3.5, 4.0]:
            for max_tpd in [None, 3, 5, 8]:
                configs.append({
                    "risk": risk, "daily_cap": daily_cap, "max_tpd": max_tpd,
                    "label": f"R{risk:.1f}_DC{daily_cap or 'N'}_MT{max_tpd or 'N'}",
                })

    results = []
    print(f"\n  Testing {len(configs)} configurations on {len(test_windows)} windows...")

    for i, cfg in enumerate(configs):
        funded = 0
        p1_pass = 0
        dd_fails = 0

        for start in test_windows:
            r = simulate_exam_v2(
                streams, BEST_DECK, cfg["risk"], start,
                daily_loss_cap=cfg["daily_cap"],
                max_trades_per_day=cfg["max_tpd"],
            )
            if r["exam"] == "FUNDED":
                funded += 1
            if r["p1"]["outcome"] == "PASS":
                p1_pass += 1
            if "DD" in r["p1"]["outcome"]:
                dd_fails += 1

        n = len(test_windows)
        results.append({
            **cfg, "funded": funded, "funded_rate": funded / n * 100,
            "p1_pass": p1_pass, "p1_rate": p1_pass / n * 100,
            "dd_fails": dd_fails, "dd_rate": dd_fails / n * 100, "n": n,
        })

        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(configs)}...", flush=True)

    results.sort(key=lambda x: -x["funded_rate"])

    print(f"\n  TOP 20 CONFIGS (2023-2025):")
    print(f"  {'Config':<25s} {'P1':>8s} {'P1%':>6s} {'Funded':>8s} {'Fund%':>6s} {'DD%':>5s}")
    print(f"  {'-'*70}")

    for r in results[:20]:
        print(f"  {r['label']:<25s} {r['p1_pass']:>3d}/{r['n']:<4d} {r['p1_rate']:>5.1f}% "
              f"{r['funded']:>3d}/{r['n']:<4d} {r['funded_rate']:>5.1f}% {r['dd_rate']:>4.0f}%")

    # ═══════════════════════════════════════════════════════════════
    # VALIDATE BEST FIX on pure 2025 OOS
    # ═══════════════════════════════════════════════════════════════
    if results:
        best = results[0]
        print(f"\n{'='*120}")
        print(f"  BEST FIX: {best['label']}")
        print(f"  2023-2025 funded rate: {best['funded_rate']:.1f}%")
        print(f"{'='*120}")

        # Now test ONLY on 2025
        print(f"\n  Validating on pure 2025 OOS...")
        oos_windows = pd.date_range("2025-01-01", "2025-10-01", freq="30D")

        print(f"\n  {'Window':<12s} {'P1':>12s} {'P1 Profit':>10s} {'P1 DD':>6s} "
              f"{'P2':>12s} {'P2 Profit':>10s} {'Exam':>10s}")
        print(f"  {'-'*80}")

        oos_funded = 0
        oos_p1 = 0
        for start in oos_windows:
            r = simulate_exam_v2(
                streams, BEST_DECK, best["risk"], start,
                daily_loss_cap=best["daily_cap"],
                max_trades_per_day=best["max_tpd"],
            )
            p1 = r["p1"]
            p2_out = r["p2"]["outcome"] if r["p2"] else "-"
            p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
            marker = " <<<" if r["exam"] == "FUNDED" else ""

            print(f"  {str(start.date()):<12s} {p1['outcome']:>12s} {p1['profit_pct']:>+9.1f}% "
                  f"{p1['max_dd']:>5.1f}% {p2_out:>12s} {p2_prof:>10s} {r['exam']:>10s}{marker}")

            if r["exam"] == "FUNDED":
                oos_funded += 1
            if p1["outcome"] == "PASS":
                oos_p1 += 1

        n_oos = len(oos_windows)
        print(f"\n  2025 OOS with fix: P1={oos_p1}/{n_oos} ({oos_p1/n_oos*100:.1f}%) | "
              f"FUNDED={oos_funded}/{n_oos} ({oos_funded/n_oos*100:.1f}%)")

        # Also test top 5 on 2025 alone
        print(f"\n  TOP 5 configs → 2025 OOS:")
        print(f"  {'Config':<25s} {'2023-25':>8s} {'2025 P1':>8s} {'2025 Fund':>10s}")
        print(f"  {'-'*55}")

        for r in results[:5]:
            oos_f = 0
            oos_p = 0
            for start in oos_windows:
                res = simulate_exam_v2(
                    streams, BEST_DECK, r["risk"], start,
                    daily_loss_cap=r["daily_cap"],
                    max_trades_per_day=r["max_tpd"],
                )
                if res["exam"] == "FUNDED":
                    oos_f += 1
                if res["p1"]["outcome"] == "PASS":
                    oos_p += 1

            print(f"  {r['label']:<25s} {r['funded_rate']:>7.1f}% "
                  f"{oos_p}/{n_oos} ({oos_p/n_oos*100:>4.1f}%) "
                  f"{oos_f}/{n_oos} ({oos_f/n_oos*100:>4.1f}%)")

    # ═══════════════════════════════════════════════════════════════
    # ADDITIONAL: Test different deck compositions on 2025
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  DECK COMPOSITION TEST: Which combos still work in 2025?")
    print(f"{'='*120}")

    # Test each combo individually in 2025
    print(f"\n  Individual combo PnL in 2025 (at 3% risk):")
    combo_2025_pnl = {}
    for combo in BEST_DECK:
        if combo not in streams:
            continue
        trades_2025 = [t for t in streams[combo] if t["date"].year == 2025]
        total_pnl = sum(t["pnl"] * 3.0 for t in trades_2025)
        pnl_pct = total_pnl / 100000 * 100
        n_trades = len(trades_2025)
        combo_2025_pnl[combo] = {"pnl_pct": pnl_pct, "trades": n_trades}
        marker = " +" if pnl_pct > 0 else " -"
        print(f"    {combo:<35s} {pnl_pct:>+7.2f}% ({n_trades} trades){marker}")

    # Build 2025-optimized deck: only profitable combos
    profitable_2025 = [c for c, v in combo_2025_pnl.items() if v["pnl_pct"] > 0]
    print(f"\n  Profitable in 2025: {len(profitable_2025)} combos")
    for c in profitable_2025:
        print(f"    {c}")

    if profitable_2025:
        print(f"\n  Testing profitable-2025 deck across risk levels:")
        for risk in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
            for daily_cap in [None, 3.0, 4.0]:
                funded = 0
                p1_p = 0
                for start in oos_windows:
                    r = simulate_exam_v2(streams, profitable_2025, risk, start,
                                         daily_loss_cap=daily_cap)
                    if r["exam"] == "FUNDED":
                        funded += 1
                    if r["p1"]["outcome"] == "PASS":
                        p1_p += 1
                dc_str = f"DC{daily_cap}" if daily_cap else "DC_N"
                print(f"    R{risk:.1f} {dc_str}: P1={p1_p}/{n_oos} ({p1_p/n_oos*100:.0f}%) "
                      f"FUNDED={funded}/{n_oos} ({funded/n_oos*100:.0f}%)")


if __name__ == "__main__":
    main()
