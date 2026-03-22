"""
Deck optimizer: find the best combo selection + risk allocation for FTMO challenges.

Three optimizations:
1. Risk budget: total_risk / n_combos (avoid DD stacking)
2. Correlation-aware selection: avoid combos that lose together
3. Grid search: deck_size × risk_per_trade × combo_selection

Usage:
    python -X utf8 scripts/optimize_deck.py
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
from itertools import combinations

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# Import combo definitions from challenge_decks
from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY

WINDOW_STARTS = pd.date_range("2015-01-01", "2024-06-01", freq="90D")


def load_strategy(entry):
    import importlib
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trade_streams(config, instruments, data_dict):
    """
    Pre-compute trade PnL streams for every combo at 1% risk.
    We can then scale linearly for different risk levels.
    """
    print("\n  Pre-computing trade streams for all combos...")
    streams = {}

    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.01  # Base 1%, scale later
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )

    for combo_name, entry in ALL_COMBOS.items():
        sym = entry["symbol"]
        if sym not in data_dict:
            print(f"    {combo_name}: NO DATA")
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
        print(f"    {combo_name}: {len(trades)} trades")

    return streams


def simulate_exam(streams, combo_names, risk_mult, start_date,
                  initial=100000, p1_days=30, p2_days=60,
                  p1_target=10.0, p2_target=5.0):
    """
    Fast exam simulation using pre-computed trade streams.
    risk_mult: multiplier vs 1% base (e.g., 2.0 = 2% risk per trade)
    """
    def run_phase(phase_start, window_days, target_pct):
        phase_end = phase_start + timedelta(days=window_days)
        equity = initial
        peak = initial
        daily_start = initial
        trading_days = set()
        current_day = None
        max_dd = 0
        max_daily_dd = 0
        locked = False
        days_used = window_days

        # Collect all trades in window, sorted by time
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

            # Daily DD reset
            if t["date"] != current_day:
                if current_day is not None:
                    daily_dd = (daily_start - equity) / initial
                    max_daily_dd = max(max_daily_dd, daily_dd)
                current_day = t["date"]
                daily_start = equity

            # Apply PnL (scaled by risk multiplier)
            pnl = t["pnl"] * risk_mult
            equity += pnl
            trading_days.add(t["date"])

            # Check DD (static from initial)
            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)

            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": max_daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            # Check daily DD
            daily_dd = (daily_start - equity) / initial
            if daily_dd >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            # Profit lock
            profit_pct = (equity - initial) / initial * 100
            if profit_pct >= target_pct and len(trading_days) >= 4:
                locked = True
                days_used = (t["date"] - phase_start.date()).days + 1

        # Final daily DD check
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

    # Phase 1
    p1 = run_phase(start_date, p1_days, p1_target)
    if p1["outcome"] != "PASS":
        return {"exam": f"FAIL_P1", "p1": p1, "p2": None}

    # Phase 2
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, p2_target)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    else:
        return {"exam": f"FAIL_P2", "p1": p1, "p2": p2}


def eval_deck(streams, combo_names, risk_mult, verbose=False):
    """Evaluate a deck across all windows. Returns funded rate."""
    funded = 0
    p1_pass = 0
    dd_fails = 0
    n = len(WINDOW_STARTS)

    for start in WINDOW_STARTS:
        r = simulate_exam(streams, combo_names, risk_mult, start)
        if r["exam"] == "FUNDED":
            funded += 1
        if r["p1"]["outcome"] == "PASS":
            p1_pass += 1
        if "DD" in r["p1"]["outcome"]:
            dd_fails += 1

    return {
        "funded": funded, "funded_rate": funded / n * 100,
        "p1_pass": p1_pass, "p1_rate": p1_pass / n * 100,
        "dd_fails": dd_fails, "dd_rate": dd_fails / n * 100,
        "n": n,
    }


def compute_correlation_matrix(streams):
    """Compute daily PnL correlation between combos."""
    # Build daily PnL series for each combo
    daily_pnl = {}
    for combo, trades in streams.items():
        if not trades:
            continue
        df = pd.DataFrame(trades)
        daily = df.groupby("date")["pnl"].sum()
        daily_pnl[combo] = daily

    if len(daily_pnl) < 2:
        return pd.DataFrame()

    # Align on common dates
    combined = pd.DataFrame(daily_pnl).fillna(0)
    return combined.corr()


def greedy_decorrelated_deck(streams, corr_matrix, max_size, candidates=None):
    """Build a deck by greedily adding the least correlated combo."""
    if candidates is None:
        candidates = list(streams.keys())
    candidates = [c for c in candidates if c in corr_matrix.columns]

    if not candidates:
        return []

    # Start with highest PF combo
    best_start = max(candidates, key=lambda c: ALL_COMBOS[c]["pf"])
    deck = [best_start]
    remaining = [c for c in candidates if c != best_start]

    while len(deck) < max_size and remaining:
        # For each remaining combo, compute avg correlation with current deck
        best_next = None
        best_score = float('inf')

        for c in remaining:
            avg_corr = np.mean([abs(corr_matrix.loc[c, d]) for d in deck
                                if c in corr_matrix.index and d in corr_matrix.columns])
            # Score = avg_corr - PF_bonus (prefer low correlation AND high PF)
            pf_bonus = (ALL_COMBOS[c]["pf"] - 1.0) * 0.5
            score = avg_corr - pf_bonus
            if score < best_score:
                best_score = score
                best_next = c

        if best_next:
            deck.append(best_next)
            remaining.remove(best_next)

    return deck


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({e["symbol"] for e in ALL_COMBOS.values()})
    data_dict = {}
    print("Loading data...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
            print(f"  {sym}: {len(data_dict[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Step 1: Pre-compute trade streams at 1% risk
    streams = precompute_trade_streams(config, instruments, data_dict)

    # Step 2: Correlation matrix
    print("\n  Computing correlation matrix...")
    corr = compute_correlation_matrix(streams)
    if not corr.empty:
        print(f"    {len(corr)} combos in correlation matrix")
        # Show highest correlations
        pairs = []
        for i in range(len(corr)):
            for j in range(i + 1, len(corr)):
                c1, c2 = corr.index[i], corr.columns[j]
                pairs.append((c1, c2, corr.iloc[i, j]))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        print("    Top 10 correlated pairs:")
        for c1, c2, r in pairs[:10]:
            print(f"      {c1:30s} <-> {c2:30s}  r={r:+.3f}")
        print("    Top 10 anti-correlated pairs:")
        for c1, c2, r in sorted(pairs, key=lambda x: x[2])[:10]:
            print(f"      {c1:30s} <-> {c2:30s}  r={r:+.3f}")

    # Step 3: Grid search — deck_size × risk_per_trade
    print(f"\n{'='*120}")
    print(f"  GRID SEARCH: Optimal deck composition + risk level")
    print(f"{'='*120}")

    robust_combos = [k for k, v in ALL_COMBOS.items() if v["tier"] == "ROBUST"]
    all_combos = list(ALL_COMBOS.keys())

    # Build decorrelated decks of various sizes
    decorr_decks = {}
    for size in [3, 4, 5, 6, 8, 10, 12, 16]:
        if size <= len(robust_combos):
            deck = greedy_decorrelated_deck(streams, corr, size, robust_combos)
            decorr_decks[f"Decorr{size}_R"] = deck
        if size <= len(all_combos):
            deck = greedy_decorrelated_deck(streams, corr, size, all_combos)
            decorr_decks[f"Decorr{size}_A"] = deck

    # Fixed decks
    fixed_decks = {
        "Core3": ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1"],
        "Robust16": robust_combos,
        "Full27": all_combos,
    }

    all_decks = {**fixed_decks, **decorr_decks}

    # Risk levels to test (as multiplier of 1% base)
    risk_levels = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    results = []
    print(f"\n  Testing {len(all_decks)} decks × {len(risk_levels)} risk levels = {len(all_decks)*len(risk_levels)} configs...\n")

    for deck_name, combo_names in all_decks.items():
        valid_combos = [c for c in combo_names if c in streams]
        if not valid_combos:
            continue

        print(f"  {deck_name} ({len(valid_combos)} combos):", end=" ", flush=True)

        best_funded_rate = 0
        best_risk = 0

        for risk_mult in risk_levels:
            r = eval_deck(streams, valid_combos, risk_mult)
            results.append({
                "deck": deck_name, "combos": len(valid_combos),
                "risk": f"{risk_mult:.1f}%", "risk_mult": risk_mult,
                **r,
            })
            if r["funded_rate"] > best_funded_rate:
                best_funded_rate = r["funded_rate"]
                best_risk = risk_mult

        print(f"best={best_funded_rate:.1f}% @{best_risk:.1f}% risk")

    # Step 4: Summary — top 20 configs
    results.sort(key=lambda x: (-x["funded_rate"], -x["p1_rate"]))

    print(f"\n{'='*120}")
    print(f"  TOP 20 CONFIGURATIONS")
    print(f"{'='*120}")
    print(f"  {'Deck':<20s} {'#':>3s} {'Risk':>5s} "
          f"{'P1':>8s} {'P1%':>6s} {'FUNDED':>8s} {'Fund%':>6s} {'DD%':>5s} "
          f"{'Combos'}")
    print(f"  {'-'*115}")

    seen = set()
    printed = 0
    for r in results:
        if printed >= 20:
            break
        # Skip duplicates (same deck at slightly worse risk)
        key = r["deck"]
        if key in seen:
            continue
        seen.add(key)

        combo_names = all_decks[r["deck"]]
        valid = [c for c in combo_names if c in streams]
        combo_str = ", ".join(valid[:6])
        if len(valid) > 6:
            combo_str += f"... +{len(valid)-6}"

        print(f"  {r['deck']:<20s} {r['combos']:>3d} {r['risk']:>5s} "
              f"{r['p1_pass']:>3d}/{r['n']:<4d} {r['p1_rate']:>5.1f}% "
              f"{r['funded']:>3d}/{r['n']:<4d} {r['funded_rate']:>5.1f}% "
              f"{r['dd_rate']:>4.0f}% "
              f"{combo_str}")
        printed += 1

    # Step 5: Absolute best config — detailed breakdown
    if results:
        best = results[0]
        deck_name = best["deck"]
        risk_mult = best["risk_mult"]
        combo_names = [c for c in all_decks[deck_name] if c in streams]

        print(f"\n{'='*120}")
        print(f"  BEST CONFIG: {deck_name} @ {risk_mult:.1f}% risk")
        print(f"  Combos: {', '.join(combo_names)}")
        print(f"{'='*120}")

        # Per-window breakdown
        print(f"\n  {'Window':<12s} {'P1':>8s} {'P1 Profit':>10s} {'P1 DD':>6s} "
              f"{'P2':>8s} {'P2 Profit':>10s} {'P2 DD':>6s} {'Exam':>10s}")
        print(f"  {'-'*80}")

        for start in WINDOW_STARTS:
            r = simulate_exam(streams, combo_names, risk_mult, start)
            p1 = r["p1"]
            p2_out = r["p2"]["outcome"] if r["p2"] else "-"
            p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
            p2_dd = f"{r['p2']['max_dd']:.1f}%" if r["p2"] else "-"

            marker = " <<<" if r["exam"] == "FUNDED" else ""
            print(f"  {str(start.date()):<12s} {p1['outcome']:>8s} {p1['profit_pct']:>+9.1f}% {p1['max_dd']:>5.1f}% "
                  f"{p2_out:>8s} {p2_prof:>10s} {p2_dd:>6s} {r['exam']:>10s}{marker}")

    # Step 6: ROI for top 5
    print(f"\n{'='*120}")
    print(f"  ROI ANALYSIS — Top 5 configs")
    print(f"{'='*120}\n")

    seen2 = set()
    printed2 = 0
    for r in results:
        if printed2 >= 5:
            break
        if r["deck"] in seen2:
            continue
        seen2.add(r["deck"])
        fr = r["funded_rate"] / 100
        if fr > 0:
            cost_per = 80 / fr
            m_funded = 10 * fr
            m_income = m_funded * 400
            print(f"  {r['deck']:<20s} @{r['risk']:>5s}: "
                  f"{r['funded_rate']:5.1f}% funded | "
                  f"EUR{cost_per:,.0f}/funded | "
                  f"10 exams/mo -> {m_funded:.1f} funded -> EUR{m_income:,.0f}/mo")
        printed2 += 1


if __name__ == "__main__":
    main()
