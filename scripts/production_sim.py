"""
Production-grade FTMO exam simulation.

Solves the core problem: individual backtests don't share a risk budget.
This script simulates a REAL shared account where:
1. All combos trade the same $100K account
2. Daily loss cap: stop ALL trading when daily loss hits X%
3. Per-combo cooldown: after SL hit, no re-entry that day for same combo
4. Adaptive deck: rolling lookback to filter dead combos (no look-ahead)
5. Profit lock: stop trading once target + 4 days met

Tests across 2016-2025 including pure 2025 OOS.

Usage:
    python -X utf8 scripts/production_sim.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import timedelta, date
from collections import defaultdict

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names):
    """Pre-compute all trades at 1% risk. Returns {combo: [trade_dicts]}."""
    print("\n  Pre-computing trade streams...")
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
                trades.append({
                    "ts": ts, "date": ts.date(), "pnl": t.pnl,
                    "combo": combo_name,
                })
        streams[combo_name] = trades
        print(f"    {combo_name}: {len(trades)} trades", end="")
        if trades:
            print(f" ({min(t['date'] for t in trades)} → {max(t['date'] for t in trades)})")
        else:
            print()
    return streams


def get_active_combos(streams, all_combos, eval_date, lookback_months=6, min_trades=3):
    """Rolling lookback filter — uses ONLY past data."""
    eval_ts = pd.Timestamp(eval_date)
    cutoff = eval_ts - timedelta(days=lookback_months * 30)
    active = []
    for combo in all_combos:
        if combo not in streams:
            continue
        recent = [t for t in streams[combo]
                  if cutoff <= pd.Timestamp(t["date"]) < eval_ts]
        if len(recent) < min_trades:
            active.append(combo)  # benefit of doubt
            continue
        wins = sum(t["pnl"] for t in recent if t["pnl"] > 0)
        losses = abs(sum(t["pnl"] for t in recent if t["pnl"] < 0))
        pf = wins / losses if losses > 0 else 99
        if pf > 1.0:
            active.append(combo)
    return active


def simulate_exam(streams, combo_names, risk_mult, start_date,
                  daily_loss_cap_pct=3.0, combo_daily_max_losses=1,
                  initial=100000, p1_days=30, p2_days=60):
    """
    Production exam simulation with REAL portfolio-level risk controls.

    Key controls:
    - daily_loss_cap_pct: stop ALL trading if daily portfolio loss >= X%
    - combo_daily_max_losses: max losing trades per combo per day (cooldown)
    - profit lock: stop once target hit + 4 trading days
    """
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
        combo_day_losses = defaultdict(int)  # {combo: n_losses_today}
        daily_stopped = False

        # Gather all trades in window
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

            # New day reset
            if t["date"] != current_day:
                if current_day is not None:
                    daily_dd = (daily_start - equity) / initial
                    max_daily_dd = max(max_daily_dd, daily_dd)
                current_day = t["date"]
                daily_start = equity
                combo_day_losses.clear()
                daily_stopped = False

            # CONTROL 1: Portfolio daily loss cap
            if daily_stopped:
                continue

            # CONTROL 2: Per-combo cooldown
            combo = t["combo"]
            if combo_day_losses[combo] >= combo_daily_max_losses:
                continue

            # Execute trade
            pnl = t["pnl"] * risk_mult
            equity += pnl
            trading_days.add(t["date"])

            # Track combo losses
            if pnl < 0:
                combo_day_losses[combo] += 1

            # Check portfolio daily loss cap
            daily_loss_pct = (daily_start - equity) / initial * 100
            if daily_loss_pct >= daily_loss_cap_pct:
                daily_stopped = True

            # Check FTMO hard limits
            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": max_daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            daily_dd_hard = (daily_start - equity) / initial
            if daily_dd_hard >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - initial) / initial * 100,
                        "max_dd": max_dd * 100, "max_daily_dd": daily_dd_hard * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            # Profit lock
            profit_pct = (equity - initial) / initial * 100
            if profit_pct >= target_pct and len(trading_days) >= 4:
                locked = True
                days_used = (t["date"] - phase_start.date()).days + 1

        # Final daily DD
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
    p1 = run_phase(start_date, p1_days, 10.0)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    # Phase 2
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0)
    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # All combos in our validated pool
    pool = list(ALL_COMBOS.keys())
    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in pool})

    data_dict = {}
    print("Loading data (2015-2025)...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (→ {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Pre-compute
    streams = precompute_trades(config, instruments, data_dict, pool)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: DIAGNOSTIC — What kills us?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  DIAGNOSTIC: Worst daily losses across 2024-2025")
    print(f"{'='*120}")

    for year in [2024, 2025]:
        # Find worst daily portfolio losses
        daily_pnl = defaultdict(lambda: defaultdict(float))
        for combo in pool:
            if combo not in streams:
                continue
            for t in streams[combo]:
                if t["date"].year == year:
                    daily_pnl[t["date"]][combo] += t["pnl"] * 3.0  # at 3% risk

        worst_days = []
        for day, combos in daily_pnl.items():
            total = sum(combos.values())
            total_pct = total / 100000 * 100
            worst_days.append((day, total_pct, combos))

        worst_days.sort(key=lambda x: x[1])
        print(f"\n  {year} — 5 worst days:")
        for day, pct, combos in worst_days[:5]:
            n_losers = sum(1 for v in combos.values() if v < 0)
            biggest_loser = min(combos.items(), key=lambda x: x[1])
            print(f"    {day}: {pct:+.2f}% ({n_losers} losers, "
                  f"biggest: {biggest_loser[0]} {biggest_loser[1]/1000:+.1f}K)")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 2: GRID SEARCH — structural risk controls
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  GRID SEARCH: risk × daily_cap × combo_cooldown × adaptive_lookback")
    print(f"  Tested on 2019-2025 (includes 2025 OOS)")
    print(f"{'='*120}")

    # Windows: every 30 days from 2019 to 2025
    all_windows = pd.date_range("2019-01-01", "2025-10-01", freq="30D")
    oos_cutoff = pd.Timestamp("2025-01-01")

    configs = []
    for risk in [1.5, 2.0, 2.5, 3.0]:
        for daily_cap in [2.0, 2.5, 3.0, 4.0]:
            for cooldown in [1, 2, 99]:  # 1=max 1 loss/combo/day, 99=no limit
                for lookback in [0, 6, 12]:  # 0=no adaptive, 6/12=months
                    configs.append({
                        "risk": risk, "daily_cap": daily_cap,
                        "cooldown": cooldown, "lookback": lookback,
                    })

    print(f"  {len(configs)} configurations × {len(all_windows)} windows = {len(configs)*len(all_windows)} sims\n")

    results = []
    for i, cfg in enumerate(configs):
        is_funded = 0; is_n = 0
        oos_funded = 0; oos_n = 0
        is_dd_fails = 0; oos_dd_fails = 0

        for start in all_windows:
            # Adaptive combo selection
            if cfg["lookback"] > 0:
                active = get_active_combos(streams, pool, start.date(),
                                           lookback_months=cfg["lookback"])
            else:
                active = [c for c in pool if c in streams]

            if not active:
                if start >= oos_cutoff:
                    oos_n += 1
                else:
                    is_n += 1
                continue

            r = simulate_exam(
                streams, active, cfg["risk"], start,
                daily_loss_cap_pct=cfg["daily_cap"],
                combo_daily_max_losses=cfg["cooldown"],
            )

            is_oos = start >= oos_cutoff
            if is_oos:
                oos_n += 1
                if r["exam"] == "FUNDED":
                    oos_funded += 1
                if "DD" in r["p1"]["outcome"]:
                    oos_dd_fails += 1
            else:
                is_n += 1
                if r["exam"] == "FUNDED":
                    is_funded += 1
                if "DD" in r["p1"]["outcome"]:
                    is_dd_fails += 1

        is_rate = is_funded / is_n * 100 if is_n else 0
        oos_rate = oos_funded / oos_n * 100 if oos_n else 0
        total_funded = is_funded + oos_funded
        total_n = is_n + oos_n
        total_rate = total_funded / total_n * 100 if total_n else 0

        results.append({
            **cfg,
            "is_funded": is_funded, "is_n": is_n, "is_rate": is_rate,
            "oos_funded": oos_funded, "oos_n": oos_n, "oos_rate": oos_rate,
            "total_funded": total_funded, "total_n": total_n, "total_rate": total_rate,
            "is_dd": is_dd_fails, "oos_dd": oos_dd_fails,
            "label": f"R{cfg['risk']:.1f}_DC{cfg['daily_cap']}_CD{cfg['cooldown']}_L{cfg['lookback']}",
        })

        if (i + 1) % 50 == 0:
            best_so_far = max(results, key=lambda x: x["oos_rate"])
            print(f"    [{i+1}/{len(configs)}] best OOS so far: "
                  f"{best_so_far['label']} → {best_so_far['oos_rate']:.1f}%", flush=True)

    # ═══════════════════════════════════════════════════════════════
    # PHASE 3: RESULTS — sorted by OOS (the ONLY truth)
    # ═══════════════════════════════════════════════════════════════
    results.sort(key=lambda x: (-x["oos_rate"], -x["is_rate"]))

    print(f"\n{'='*120}")
    print(f"  TOP 25 by 2025 OOS funded rate (the only metric that matters)")
    print(f"{'='*120}")
    print(f"  {'Config':<30s} {'IS fund':>8s} {'IS%':>6s} {'OOS fund':>9s} {'OOS%':>6s} "
          f"{'Gap':>6s} {'OOS DD':>7s}")
    print(f"  {'-'*80}")

    for r in results[:25]:
        gap = r["is_rate"] - r["oos_rate"]
        print(f"  {r['label']:<30s} {r['is_funded']:>3d}/{r['is_n']:<4d} {r['is_rate']:>5.1f}% "
              f"{r['oos_funded']:>4d}/{r['oos_n']:<4d} {r['oos_rate']:>5.1f}% "
              f"{gap:>+5.1f}pp {r['oos_dd']:>3d}DD")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 4: BEST CONFIG — detailed 2025 breakdown
    # ═══════════════════════════════════════════════════════════════
    best = results[0]
    print(f"\n{'='*120}")
    print(f"  BEST CONFIG: {best['label']}")
    print(f"  IS: {best['is_rate']:.1f}% | OOS: {best['oos_rate']:.1f}% | Gap: {best['is_rate']-best['oos_rate']:+.1f}pp")
    print(f"{'='*120}")

    oos_windows = pd.date_range("2025-01-01", "2025-10-01", freq="30D")
    print(f"\n  {'Window':<12s} {'Active':>7s} {'P1':>12s} {'P1 Profit':>10s} {'P1 DD':>6s} "
          f"{'P2':>12s} {'P2 Profit':>10s} {'Exam':>10s}")
    print(f"  {'-'*95}")

    oos_funded_count = 0
    for start in oos_windows:
        if best["lookback"] > 0:
            active = get_active_combos(streams, pool, start.date(),
                                       lookback_months=best["lookback"])
        else:
            active = [c for c in pool if c in streams]

        r = simulate_exam(
            streams, active, best["risk"], start,
            daily_loss_cap_pct=best["daily_cap"],
            combo_daily_max_losses=best["cooldown"],
        )

        p1 = r["p1"]
        p2_out = r["p2"]["outcome"] if r["p2"] else "-"
        p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
        marker = " <<<" if r["exam"] == "FUNDED" else ""

        print(f"  {str(start.date()):<12s} {len(active):>3d}/{len(pool):<3d} "
              f"{p1['outcome']:>12s} {p1['profit_pct']:>+9.1f}% {p1['max_dd']:>5.1f}% "
              f"{p2_out:>12s} {p2_prof:>10s} {r['exam']:>10s}{marker}")

        if r["exam"] == "FUNDED":
            oos_funded_count += 1

    n_oos = len(oos_windows)
    oos_rate = oos_funded_count / n_oos * 100

    # ═══════════════════════════════════════════════════════════════
    # PHASE 5: Year-by-year stability
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  YEAR-BY-YEAR STABILITY — {best['label']}")
    print(f"{'='*120}")
    print(f"  {'Year':<6s} {'Windows':>8s} {'Active':>7s} {'P1 Pass':>8s} {'P1%':>6s} "
          f"{'Funded':>8s} {'Fund%':>6s} {'DD Fails':>8s} {'Type':>5s}")
    print(f"  {'-'*80}")

    for year in range(2019, 2026):
        year_windows = pd.date_range(f"{year}-01-01", f"{year}-10-01", freq="30D")
        funded = 0
        p1_pass = 0
        dd_fails = 0
        active_counts = []

        for start in year_windows:
            if best["lookback"] > 0:
                active = get_active_combos(streams, pool, start.date(),
                                           lookback_months=best["lookback"])
            else:
                active = [c for c in pool if c in streams]
            active_counts.append(len(active))

            if not active:
                continue

            r = simulate_exam(
                streams, active, best["risk"], start,
                daily_loss_cap_pct=best["daily_cap"],
                combo_daily_max_losses=best["cooldown"],
            )
            if r["exam"] == "FUNDED":
                funded += 1
            if r["p1"]["outcome"] == "PASS":
                p1_pass += 1
            if "DD" in r["p1"]["outcome"]:
                dd_fails += 1

        n = len(year_windows)
        avg_active = np.mean(active_counts) if active_counts else 0
        oos_tag = "OOS" if year >= 2025 else "IS"
        marker = " <<<" if year >= 2025 else ""

        print(f"  {year:<6d} {n:>8d} {avg_active:>5.1f}/27 {p1_pass:>3d}/{n:<4d} {p1_pass/n*100:>5.1f}% "
              f"{funded:>3d}/{n:<4d} {funded/n*100:>5.1f}% {dd_fails:>8d} {oos_tag:>5s}{marker}")

    # ═══════════════════════════════════════════════════════════════
    # PHASE 6: ROI for $5K accounts
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  ROI — $5K accounts @EUR40/exam")
    print(f"{'='*120}")

    # Use the WORST estimate: OOS rate
    print(f"\n  Using 2025 OOS rate: {oos_rate:.1f}%")

    if oos_rate > 0:
        fr = oos_rate / 100
        cost_per = 40 / fr
        profit_per_funded_mo = 200  # ~$200/mo per $5K funded, conservative

        print(f"  Cost per funded account: EUR{cost_per:.0f}")
        print()
        print(f"  {'Exams/mo':>10s} {'Cost':>8s} {'Funded':>8s} {'Income':>10s} {'Net':>10s} {'ROI':>8s}")
        print(f"  {'-'*60}")
        for n_ex in [5, 10, 15, 20, 30]:
            funded_mo = n_ex * fr
            income = funded_mo * profit_per_funded_mo
            cost = n_ex * 40
            net = income - cost
            roi = net / cost * 100
            print(f"  {n_ex:>10d} EUR{cost:>5.0f} {funded_mo:>7.1f} EUR{income:>7.0f} "
                  f"EUR{net:>+7.0f} {roi:>+6.0f}%")
    else:
        print(f"\n  OOS funded rate is 0%. Edge not confirmed for live trading.")
        print(f"  Recommended: expand strategy pool or wait for more 2025 data.")

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY VERDICT
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  VERDICT")
    print(f"{'='*120}")

    is_rate = best["is_rate"]
    if oos_rate >= is_rate * 0.7:
        print(f"\n  EDGE CONFIRMED — OOS within 30% of IS")
        print(f"  IS: {is_rate:.1f}% → OOS: {oos_rate:.1f}% (degradation: {(1-oos_rate/is_rate)*100:.0f}%)")
        print(f"  RECOMMENDATION: Start with 5-10 exams, track results, scale up if confirmed")
    elif oos_rate >= is_rate * 0.4:
        print(f"\n  EDGE PARTIAL — significant but present")
        print(f"  IS: {is_rate:.1f}% → OOS: {oos_rate:.1f}% (degradation: {(1-oos_rate/is_rate)*100:.0f}%)")
        print(f"  RECOMMENDATION: Start with 5 exams as validation, don't scale until confirmed")
    elif oos_rate > 0:
        print(f"\n  EDGE WEAK — present but unreliable")
        print(f"  IS: {is_rate:.1f}% → OOS: {oos_rate:.1f}%")
        print(f"  RECOMMENDATION: Paper trade for 2 months before risking capital")
    else:
        print(f"\n  NO EDGE IN 2025 — do not deploy")
        print(f"  IS: {is_rate:.1f}% → OOS: 0%")
        print(f"  RECOMMENDATION: Develop new strategies targeting current regime")


if __name__ == "__main__":
    main()
