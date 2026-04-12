"""
Fase 3+4 — Sweep exam parameters + walk-forward combo selection.

Two goals:
  1. Find aggressive exam config that maximizes funded rate (asymmetric risk)
  2. Walk-forward: only keep combos with edge in rolling test windows

Reuses core functions from production_sim (precompute_trades, simulate_exam, etc.)

Usage:
    python -X utf8 scripts/optimize_exam_params.py
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
from itertools import product as cartesian

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY

# Import core sim functions
from scripts.production_sim import precompute_trades, simulate_exam, _size_trade


# ═══════════════════════════════════════════════════════════════
# ALIVE DECK — same as production_sim
# ═══════════════════════════════════════════════════════════════
ALIVE_DECK = [
    "VMR_NZDUSD_wideR_H4_ny", "MACross_XAUUSD_trend_H4_ny",
    "IBB_NZDUSD_trend_H4", "Engulf_EURUSD_trend_H4",
    "ADXbirth_XTIUSD_slow_ema_H4", "EMArib_EURJPY_tight_H1",
    "MACross_AUDUSD_megaT_H4", "MACross_XAUUSD_wideR_H4_ny",
    "PinBar_EURJPY_deep_H4", "StrBrk_GBPJPY_wideR_H4",
    "VMR_USDCHF_default_H1_ny", "MomDiv_AUDUSD_wideR_H4",
    "StochRev_AUDUSD_calm_H4", "VMR_USDJPY_wideR_H4_ny",
    "EMArib_AUDUSD_trend_H4_lon", "MACross_EURUSD_wideR_H4_lon",
    "RegVMR_NZDUSD_default_H1_ny", "MACDhist_EURJPY_trend_H4",
    "MACross_USDCHF_trend_H4_ny", "RSIext_EURJPY_wideR_H4",
    "TPB_XTIUSD_trend_H4_ny", "MACross_USDJPY_wideR_H4_lon",
    "MACross_USDCHF_megaT_H4", "MACross_NZDUSD_trend_H4_lon",
    "TPB_NZDUSD_loose_H4_ny",
]


def walk_forward_filter(streams, combo_names, train_end_year=2023, test_years=2):
    """Walk-forward: keep combos profitable in rolling test windows.

    For each combo, check PF in the last `test_years` years before OOS.
    This avoids selecting combos that only worked historically.

    Returns filtered combo list.
    """
    test_start = pd.Timestamp(f"{train_end_year + 1}-01-01")
    test_end = pd.Timestamp(f"{train_end_year + test_years}-12-31")

    surviving = []
    for combo in combo_names:
        if combo not in streams:
            continue
        test_trades = [t for t in streams[combo]
                       if test_start.date() <= t["date"] <= test_end.date()]
        if len(test_trades) < 3:
            # Too few trades to judge — keep if historically strong
            all_pips = sum(t["pnl_pips"] for t in streams[combo])
            if all_pips > 0:
                surviving.append(combo)
            continue

        wins = sum(1 for t in test_trades if t["pnl_pips"] > 0)
        gross_p = sum(t["pnl_pips"] for t in test_trades if t["pnl_pips"] > 0)
        gross_l = abs(sum(t["pnl_pips"] for t in test_trades if t["pnl_pips"] <= 0))
        pf = gross_p / gross_l if gross_l > 0 else 99.0

        if pf >= 1.0:
            surviving.append(combo)

    return surviving


def run_exam_sweep(streams, deck, is_starts, oos_starts, configs):
    """Run exam sim for each config. Return results table."""
    results = []

    for cfg in configs:
        label = cfg["label"]

        # IS
        is_funded = 0
        is_days = []
        for start in is_starts:
            r = simulate_exam(
                streams, deck, start,
                daily_loss_cap_pct=cfg["daily_cap"],
                combo_daily_max_losses=cfg["cooldown"],
                risk_per_trade=cfg["risk_p1"],
                p2_risk_per_trade=cfg["risk_p2"],
                max_instr_per_day=cfg["max_instr"],
                max_daily_losses=cfg["max_losses"],
            )
            if r["exam"] == "FUNDED":
                is_funded += 1
                is_days.append(r["p1"]["days_used"] + r["p2"]["days_used"])

        is_rate = is_funded / len(is_starts) * 100 if is_starts.size else 0
        is_avg_days = np.mean(is_days) if is_days else 0

        # OOS
        oos_funded = 0
        oos_p1_pass = 0
        oos_days = []
        for start in oos_starts:
            r = simulate_exam(
                streams, deck, start,
                daily_loss_cap_pct=cfg["daily_cap"],
                combo_daily_max_losses=cfg["cooldown"],
                risk_per_trade=cfg["risk_p1"],
                p2_risk_per_trade=cfg["risk_p2"],
                max_instr_per_day=cfg["max_instr"],
                max_daily_losses=cfg["max_losses"],
            )
            if r["p1"]["outcome"] == "PASS":
                oos_p1_pass += 1
            if r["exam"] == "FUNDED":
                oos_funded += 1
                oos_days.append(r["p1"]["days_used"] + r["p2"]["days_used"])

        oos_rate = oos_funded / len(oos_starts) * 100 if oos_starts.size else 0
        oos_p1_rate = oos_p1_pass / len(oos_starts) * 100 if oos_starts.size else 0
        oos_avg_days = np.mean(oos_days) if oos_days else 0

        results.append({
            "label": label,
            "risk_p1": cfg["risk_p1"],
            "risk_p2": cfg["risk_p2"],
            "daily_cap": cfg["daily_cap"],
            "max_instr": cfg["max_instr"],
            "max_losses": cfg["max_losses"],
            "cooldown": cfg["cooldown"],
            "is_rate": is_rate,
            "is_avg_days": is_avg_days,
            "oos_p1": oos_p1_rate,
            "oos_rate": oos_rate,
            "oos_avg_days": oos_avg_days,
            "n_deck": len(deck),
        })

        print(f"  {label:<35s} IS={is_rate:>5.1f}% ({is_avg_days:>3.0f}d) "
              f"OOS_P1={oos_p1_rate:>5.1f}% OOS_F={oos_rate:>5.1f}% "
              f"({oos_avg_days:>3.0f}d)")

    return results


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = sorted({ALL_COMBOS[c]["symbol"] for c in ALIVE_DECK})

    data_dict = {}
    print("Loading data...")
    for sym in all_symbols:
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (-> {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Precompute all trades for full deck
    streams = precompute_trades(config, instruments, data_dict, ALIVE_DECK)

    # Date ranges
    sym_end_dates = {sym: data_dict[sym].index[-1] for sym in data_dict}
    oos_sym_dates = [d for d in sym_end_dates.values() if d.year >= 2026]
    last_data_date = min(oos_sym_dates) if oos_sym_dates else max(sym_end_dates.values())

    is_starts = pd.bdate_range("2016-01-01", "2025-09-01")
    oos_last_p1 = last_data_date - timedelta(days=30)
    oos_starts = pd.bdate_range("2026-01-02", oos_last_p1)

    print(f"\n  IS: {len(is_starts)} starts | OOS: {len(oos_starts)} starts")
    print(f"  OOS data ends: {last_data_date.date()}")

    # ══════════════════════════════════════════════════════════════
    # PHASE A: Walk-forward filter
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  WALK-FORWARD FILTER (test on 2024-2025, keep PF >= 1.0)")
    print(f"{'='*120}")

    wf_deck = walk_forward_filter(streams, ALIVE_DECK, train_end_year=2023, test_years=2)
    dropped = set(ALIVE_DECK) - set(wf_deck)

    print(f"\n  ALIVE deck: {len(ALIVE_DECK)} combos")
    print(f"  Walk-forward survivors: {len(wf_deck)} combos")
    if dropped:
        print(f"  Dropped ({len(dropped)}):")
        for c in sorted(dropped):
            trades_2425 = [t for t in streams.get(c, [])
                           if pd.Timestamp("2024-01-01").date() <= t["date"] <= pd.Timestamp("2025-12-31").date()]
            pips = sum(t["pnl_pips"] for t in trades_2425)
            print(f"    {c:<40s} ({len(trades_2425)} trades, {pips:+.0f} pips in 24-25)")

    # ══════════════════════════════════════════════════════════════
    # PHASE B: Parameter sweep on exam config
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  EXAM PARAMETER SWEEP — {len(wf_deck)} combos (walk-forward filtered)")
    print(f"{'='*120}")

    # Parameter grid — asymmetric risk exploitation
    # Key insight: exam failure = fixed €40 cost regardless of HOW you fail.
    # So maximize pass rate, even at higher individual blow-up risk.
    configs = []

    # Sweep P1 risk: 2%, 3%, 4%
    # Sweep P2 risk: 1%, 1.5%, 2%
    # Sweep daily cap: 3.5%, 5%, 7%
    # Sweep max instruments: 2, 3, 4
    # Fix cooldown=1, max_losses=3

    risk_p1_vals = [0.02, 0.03, 0.04]
    risk_p2_vals = [0.01, 0.015, 0.02]
    daily_cap_vals = [3.5, 5.0, 7.0]
    max_instr_vals = [2, 3, 4]

    for rp1, rp2, dc, mi in cartesian(risk_p1_vals, risk_p2_vals, daily_cap_vals, max_instr_vals):
        label = f"P1R{rp1*100:.0f}_P2R{rp2*100:.1f}_DC{dc}_MI{mi}"
        configs.append({
            "label": label,
            "risk_p1": rp1, "risk_p2": rp2,
            "daily_cap": dc, "max_instr": mi,
            "cooldown": 1, "max_losses": 3,
        })

    print(f"\n  Testing {len(configs)} configurations...")
    print(f"  {'Config':<35s} {'IS':>18s} {'OOS_P1':>9s} {'OOS_Fund':>9s}")
    print(f"  {'-'*80}")

    results = run_exam_sweep(streams, wf_deck, is_starts, oos_starts, configs)

    # ══════════════════════════════════════════════════════════════
    # PHASE C: Also test full ALIVE deck (no walk-forward) for comparison
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  COMPARISON: Full ALIVE deck ({len(ALIVE_DECK)} combos, no walk-forward)")
    print(f"{'='*120}")

    # Only test top 5 configs from walk-forward results (sorted by IS rate)
    top_configs = sorted(results, key=lambda x: -x["is_rate"])[:5]
    top_config_defs = []
    for r in top_configs:
        top_config_defs.append({
            "label": f"FULL_{r['label']}",
            "risk_p1": r["risk_p1"], "risk_p2": r["risk_p2"],
            "daily_cap": r["daily_cap"], "max_instr": r["max_instr"],
            "cooldown": r["cooldown"], "max_losses": r["max_losses"],
        })

    print(f"\n  {'Config':<35s} {'IS':>18s} {'OOS_P1':>9s} {'OOS_Fund':>9s}")
    print(f"  {'-'*80}")
    full_results = run_exam_sweep(streams, ALIVE_DECK, is_starts, oos_starts, top_config_defs)

    # ══════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  TOP 10 CONFIGS (sorted by IS funded rate)")
    print(f"{'='*120}")
    print(f"  {'#':>2s} {'Config':<35s} {'Deck':>4s} {'IS%':>6s} {'ISdays':>6s} "
          f"{'OOS_P1':>7s} {'OOS_F':>6s} {'OOSdays':>7s}")
    print(f"  {'-'*85}")

    all_results = results + full_results
    all_results.sort(key=lambda x: (-x["oos_rate"], -x["is_rate"]))

    for i, r in enumerate(all_results[:20]):
        print(f"  {i+1:>2d} {r['label']:<35s} {r['n_deck']:>4d} {r['is_rate']:>5.1f}% "
              f"{r['is_avg_days']:>5.0f}d {r['oos_p1']:>6.1f}% {r['oos_rate']:>5.1f}% "
              f"{r['oos_avg_days']:>6.0f}d")

    # Baseline comparison
    print(f"\n  BASELINE (current accounts.yaml): P1R2_P2R1.0_DC3.5_MI2")
    baseline = next((r for r in results if r["label"] == "P1R2_P2R1.0_DC3.5_MI2"), None)
    if baseline:
        print(f"    IS={baseline['is_rate']:.1f}% ({baseline['is_avg_days']:.0f}d) "
              f"OOS_P1={baseline['oos_p1']:.1f}% OOS_F={baseline['oos_rate']:.1f}%")

    best = all_results[0] if all_results else None
    if best:
        print(f"\n  BEST CONFIG: {best['label']}")
        print(f"    IS={best['is_rate']:.1f}% ({best['is_avg_days']:.0f}d) "
              f"OOS_P1={best['oos_p1']:.1f}% OOS_F={best['oos_rate']:.1f}%")


if __name__ == "__main__":
    main()
