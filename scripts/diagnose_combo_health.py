"""
Fase 1 — Diagnóstico: ¿Qué combos tienen edge vivo vs muerto?

Corre cada combo del pool completo (v7_expanded) año por año.
Clasifica cada combo en:
  - ALIVE: PF > 1.0 en 2024-2025 reciente
  - DYING: PF > 1.0 global pero < 1.0 en 2024-2025
  - DEAD:  PF < 1.0 global

Reports:
  1. Year-by-year PF per combo (heatmap)
  2. Recency score: PF in 2024-2025 vs 2016-2023
  3. Consistency: cuántos años con PF > 1.0
  4. Summary: combos ALIVE vs DYING vs DEAD

Usage:
    python -X utf8 scripts/diagnose_combo_health.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
import pandas as pd
import numpy as np
from copy import deepcopy

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def backtest_period(config, instruments, data_dict, combo_name, start, end):
    """Run a single combo on a date range. Return (trades, pf, wr, pnl_pips)."""
    entry = ALL_COMBOS[combo_name]
    sym = entry["symbol"]
    if sym not in data_dict:
        return None

    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.50
    cfg.risk.max_dd_limit = 0.50
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.50, 1.0)], daily_stop_threshold=0.50,
        progressive_trades=0, consecutive_win_bonus=0,
    )

    # Filter data to period
    df = data_dict[sym]
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_data = df[mask]
    if len(period_data) < 1000:  # need minimum data
        return None

    try:
        strategy = load_strategy(entry)
        engine = BacktestEngine(cfg, instruments[sym], EquityManager(eq_cfg))
        result = engine.run(strategy, period_data, sym)
    except Exception:
        return None

    trades = result.trades
    if len(trades) < 3:
        return {"n": len(trades), "pf": None, "wr": None, "pnl_pips": 0}

    wins = sum(1 for t in trades if t.pnl > 0)
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
    wr = wins / len(trades) * 100
    pnl_pips = sum(t.pnl_pips for t in trades)

    return {"n": len(trades), "pf": round(pf, 2), "wr": round(wr, 1), "pnl_pips": round(pnl_pips, 0)}


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = sorted({ALL_COMBOS[c]["symbol"] for c in ALL_COMBOS})

    data_dict = {}
    print("Loading data...")
    for sym in all_symbols:
        try:
            data_dict[sym] = loader.load(sym, start="2015-01-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (-> {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Define periods
    years = list(range(2016, 2026))
    periods = {str(y): (f"{y}-01-01", f"{y}-12-31") for y in years}
    periods["2024-25"] = ("2024-01-01", "2025-12-31")
    periods["2016-23"] = ("2016-01-01", "2023-12-31")

    combo_names = sorted(ALL_COMBOS.keys())

    # Run all combos on all periods
    print(f"\nRunning {len(combo_names)} combos × {len(periods)} periods...")
    results = {}  # combo -> period -> stats

    for i, combo in enumerate(combo_names):
        results[combo] = {}
        for period_name, (start, end) in periods.items():
            r = backtest_period(config, instruments, data_dict, combo, start, end)
            results[combo][period_name] = r
        pct = (i + 1) / len(combo_names) * 100
        recent = results[combo].get("2024-25")
        recent_pf = f"PF={recent['pf']}" if recent and recent["pf"] else "N/A"
        print(f"  [{pct:5.1f}%] {combo}: {recent_pf}")

    # ══════════════════════════════════════════════════════════════
    # REPORT 1: Year-by-year PF heatmap
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*160}")
    print(f"  YEAR-BY-YEAR PROFIT FACTOR (per combo)")
    print(f"{'='*160}")

    header = f"  {'Combo':<40s}"
    for y in years:
        header += f" {y:>6d}"
    header += f" {'16-23':>6s} {'24-25':>6s} {'Yrs>1':>5s} {'Status':>8s}"
    print(header)
    print(f"  {'-'*155}")

    alive = []
    dying = []
    dead = []

    for combo in combo_names:
        row = f"  {combo:<40s}"
        years_profitable = 0

        for y in years:
            r = results[combo].get(str(y))
            if r and r["pf"] is not None:
                pf = r["pf"]
                if pf >= 1.0:
                    years_profitable += 1
                # Color coding via markers
                if pf >= 1.5:
                    marker = f" {pf:>5.1f}+"
                elif pf >= 1.0:
                    marker = f" {pf:>5.1f} "
                elif pf >= 0.7:
                    marker = f" {pf:>5.1f}-"
                else:
                    marker = f" {pf:>5.1f}X"
                row += marker
            else:
                n = r["n"] if r else 0
                row += f"   ({n:>1d}) "

        # Summary columns
        r_old = results[combo].get("2016-23")
        r_new = results[combo].get("2024-25")
        pf_old = r_old["pf"] if r_old and r_old["pf"] else None
        pf_new = r_new["pf"] if r_new and r_new["pf"] else None

        row += f" {pf_old:>6.2f}" if pf_old else f" {'N/A':>6s}"
        row += f" {pf_new:>6.2f}" if pf_new else f" {'N/A':>6s}"
        row += f" {years_profitable:>3d}/10"

        # Classification
        if pf_new is not None and pf_new >= 1.0:
            status = "ALIVE"
            alive.append((combo, pf_new, pf_old or 0, years_profitable))
        elif pf_old is not None and pf_old >= 1.0 and (pf_new is None or pf_new < 1.0):
            status = "DYING"
            dying.append((combo, pf_new or 0, pf_old, years_profitable))
        else:
            status = "DEAD"
            dead.append((combo, pf_new or 0, pf_old or 0, years_profitable))

        row += f" {status:>8s}"
        print(row)

    # ══════════════════════════════════════════════════════════════
    # REPORT 2: Summary
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  SUMMARY")
    print(f"{'='*120}")

    print(f"\n  ALIVE ({len(alive)} combos — PF > 1.0 in 2024-2025):")
    alive.sort(key=lambda x: -x[1])
    for combo, pf_new, pf_old, yrs in alive:
        sym = ALL_COMBOS[combo]["symbol"]
        strat = ALL_COMBOS[combo]["strat"]
        tf = ALL_COMBOS[combo]["params"].get("timeframe", "?")
        trend = "↑" if pf_new >= pf_old else "↓"
        print(f"    {combo:<40s} PF24-25={pf_new:>5.2f} PF16-23={pf_old:>5.2f} "
              f"{trend} {yrs}/10yrs {strat}_{sym}_{tf}")

    print(f"\n  DYING ({len(dying)} combos — PF > 1.0 historical but < 1.0 in 2024-2025):")
    dying.sort(key=lambda x: -x[1])
    for combo, pf_new, pf_old, yrs in dying:
        sym = ALL_COMBOS[combo]["symbol"]
        strat = ALL_COMBOS[combo]["strat"]
        print(f"    {combo:<40s} PF24-25={pf_new:>5.2f} PF16-23={pf_old:>5.2f} {yrs}/10yrs")

    print(f"\n  DEAD ({len(dead)} combos — PF < 1.0 globally):")
    for combo, pf_new, pf_old, yrs in dead:
        print(f"    {combo:<40s} PF24-25={pf_new:>5.2f} PF16-23={pf_old:>5.2f} {yrs}/10yrs")

    # ══════════════════════════════════════════════════════════════
    # REPORT 3: By strategy type
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BY STRATEGY TYPE")
    print(f"{'='*120}")

    strat_stats = {}
    for combo in combo_names:
        strat = ALL_COMBOS[combo]["strat"]
        r_new = results[combo].get("2024-25")
        pf = r_new["pf"] if r_new and r_new["pf"] else 0
        if strat not in strat_stats:
            strat_stats[strat] = {"alive": 0, "dying": 0, "dead": 0, "total": 0, "pfs": []}
        strat_stats[strat]["total"] += 1
        if pf >= 1.0:
            strat_stats[strat]["alive"] += 1
        elif pf > 0:
            strat_stats[strat]["dying"] += 1
        else:
            strat_stats[strat]["dead"] += 1
        strat_stats[strat]["pfs"].append(pf)

    print(f"  {'Strategy':<12s} {'Total':>5s} {'Alive':>5s} {'Dying':>5s} {'Dead':>5s} {'AvgPF24-25':>10s}")
    print(f"  {'-'*50}")
    for strat in sorted(strat_stats, key=lambda s: -np.mean(strat_stats[s]["pfs"])):
        s = strat_stats[strat]
        avg_pf = np.mean(s["pfs"])
        print(f"  {strat:<12s} {s['total']:>5d} {s['alive']:>5d} {s['dying']:>5d} {s['dead']:>5d} {avg_pf:>10.2f}")

    # ══════════════════════════════════════════════════════════════
    # REPORT 4: By instrument
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  BY INSTRUMENT")
    print(f"{'='*120}")

    instr_stats = {}
    for combo in combo_names:
        sym = ALL_COMBOS[combo]["symbol"]
        r_new = results[combo].get("2024-25")
        pf = r_new["pf"] if r_new and r_new["pf"] else 0
        if sym not in instr_stats:
            instr_stats[sym] = {"alive": 0, "dying": 0, "total": 0, "pfs": []}
        instr_stats[sym]["total"] += 1
        if pf >= 1.0:
            instr_stats[sym]["alive"] += 1
        else:
            instr_stats[sym]["dying"] += 1
        instr_stats[sym]["pfs"].append(pf)

    print(f"  {'Instrument':<10s} {'Total':>5s} {'Alive':>5s} {'Dying':>5s} {'AvgPF24-25':>10s}")
    print(f"  {'-'*40}")
    for sym in sorted(instr_stats, key=lambda s: -np.mean(instr_stats[s]["pfs"])):
        s = instr_stats[sym]
        avg_pf = np.mean(s["pfs"])
        print(f"  {sym:<10s} {s['total']:>5d} {s['alive']:>5d} {s['dying']:>5d} {avg_pf:>10.2f}")

    # ══════════════════════════════════════════════════════════════
    # REPORT 5: Current deck health
    # ══════════════════════════════════════════════════════════════
    import yaml
    accounts_path = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"
    with open(accounts_path, "r", encoding="utf-8") as f:
        deck = yaml.safe_load(f)["deck"]

    print(f"\n{'='*120}")
    print(f"  CURRENT DECK HEALTH (24 combos from accounts.yaml)")
    print(f"{'='*120}")

    deck_alive = 0
    deck_dying = 0
    for combo in deck:
        r_new = results.get(combo, {}).get("2024-25")
        pf = r_new["pf"] if r_new and r_new["pf"] else 0
        status = "ALIVE" if pf >= 1.0 else "DYING"
        if pf >= 1.0:
            deck_alive += 1
        else:
            deck_dying += 1
        r_old = results.get(combo, {}).get("2016-23")
        pf_old = r_old["pf"] if r_old and r_old["pf"] else 0
        print(f"  {combo:<40s} PF24-25={pf:>5.2f} PF16-23={pf_old:>5.2f} {status}")

    print(f"\n  Deck: {deck_alive} ALIVE + {deck_dying} DYING out of {len(deck)}")
    print(f"  Health ratio: {deck_alive/len(deck)*100:.0f}%")


if __name__ == "__main__":
    main()
