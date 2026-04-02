"""
Production Validation — independent check of the best config from optimize_deck.py.

Takes the FIXED best config (no optimization here) and validates it:
1. Exam mode: Decorr16_A @2% DC2.5 CD1 P2x0.5
2. Funded mode: RF0.7 DC1.5 CD2
3. Full year-by-year breakdown + funded survival + ROI

This script must AGREE with optimize_deck.py results. If it doesn't, there's a bug.

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


# ═══════════════════════════════════════════════════════════════
# FIXED CONFIG — from optimize_deck.py best results
# ═══════════════════════════════════════════════════════════════

EXAM_CONFIG = {
    "daily_cap": 2.5,
    "cooldown": 1,
    "p2_rf": 0.5,
    "max_instr": 2,
    "max_losses": 3,
}

FUNDED_CONFIG = {
    "risk_factor": 0.7,
    "daily_cap": 1.5,
    "cooldown": 2,
    "max_instr": 2,
    "max_losses": 3,
}

# Decorr16_A deck (from optimize_deck.py greedy selection)
DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
    "StrBrk_GBPJPY_slow_H4", "EMArib_XNGUSD_loose_H4", "SMCOB_XAUUSD_H4",
    "SwBrk_SPY_fast_H4",
]


# ═══════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names, risk_pct=0.02):
    """Pre-compute all trades at DIRECT risk level with LOOSE DD limits."""
    print(f"\n  Pre-computing trades at {risk_pct*100:.0f}% risk (loose DD)...")
    streams = {}
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    cfg.risk.daily_dd_limit = 0.50
    cfg.risk.max_dd_limit = 0.50
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.50, 1.0)], daily_stop_threshold=0.50,
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
                trades.append({"ts": ts, "date": ts.date(), "pnl": t.pnl, "combo": combo_name})
        streams[combo_name] = trades
        if trades:
            pnl_total = sum(t["pnl"] for t in trades)
            print(f"    {combo_name}: {len(trades)} trades, PnL=${pnl_total:+,.0f}")
        else:
            print(f"    {combo_name}: 0 trades")
    return streams


def simulate_exam(streams, combo_names, start_date,
                  daily_loss_cap_pct=3.0, combo_daily_max_losses=1,
                  p2_risk_factor=1.0,
                  max_instr_per_day=99, max_daily_losses=99,
                  initial=100000, p1_days=30, p2_days=60):
    """
    FTMO 2-step exam simulation.
    Balance RESETS between phases. DD limits static from initial.
    """
    def run_phase(phase_start, window_days, target_pct, starting_equity,
                  risk_factor=1.0):
        phase_end = phase_start + timedelta(days=window_days)
        equity = starting_equity
        daily_start_eq = starting_equity
        trading_days = set()
        current_day = None
        max_dd = 0
        max_daily_dd = 0
        locked = False
        target_reached = False
        target_reached_day = None
        days_used = window_days
        combo_day_losses = defaultdict(int)
        instr_day_trades = defaultdict(int)
        total_daily_losses = 0
        daily_stopped = False

        target_equity = starting_equity + (target_pct / 100) * initial

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
                    daily_dd = (daily_start_eq - equity) / initial
                    max_daily_dd = max(max_daily_dd, daily_dd)
                current_day = t["date"]
                daily_start_eq = equity
                combo_day_losses.clear()
                instr_day_trades.clear()
                total_daily_losses = 0
                daily_stopped = False

            # Once target reached, stop real trading — just count trading days
            # (simulates micro-operations to meet min days requirement)
            if target_reached:
                trading_days.add(t["date"])
                if len(trading_days) >= 4:
                    locked = True
                    days_used = (t["date"] - phase_start.date()).days + 1
                continue

            if daily_stopped:
                continue

            combo = t["combo"]
            if combo_day_losses[combo] >= combo_daily_max_losses:
                continue

            instrument = ALL_COMBOS[combo]["symbol"]
            if instr_day_trades[instrument] >= max_instr_per_day:
                continue
            if total_daily_losses >= max_daily_losses:
                continue

            pnl = t["pnl"] * risk_factor
            equity += pnl
            trading_days.add(t["date"])
            instr_day_trades[instrument] += 1

            if pnl < 0:
                combo_day_losses[combo] += 1
                total_daily_losses += 1

            daily_loss_pct = (daily_start_eq - equity) / initial * 100
            if daily_loss_pct >= daily_loss_cap_pct:
                daily_stopped = True

            dd = (initial - equity) / initial
            max_dd = max(max_dd, dd)
            if dd >= 0.10:
                return {"outcome": "FAIL_DD", "profit_pct": (equity - starting_equity) / initial * 100,
                        "final_equity": equity, "max_dd": max_dd * 100,
                        "max_daily_dd": max_daily_dd * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            daily_dd_hard = (daily_start_eq - equity) / initial
            if daily_dd_hard >= 0.05:
                return {"outcome": "FAIL_DAILY_DD", "profit_pct": (equity - starting_equity) / initial * 100,
                        "final_equity": equity, "max_dd": max_dd * 100,
                        "max_daily_dd": daily_dd_hard * 100,
                        "trading_days": len(trading_days), "days_used": window_days}

            if equity >= target_equity:
                if len(trading_days) >= 4:
                    locked = True
                    days_used = (t["date"] - phase_start.date()).days + 1
                else:
                    target_reached = True
                    target_reached_day = t["date"]

        if current_day and not locked:
            daily_dd = (daily_start_eq - equity) / initial
            max_daily_dd = max(max_daily_dd, daily_dd)

        # If target was reached but ran out of trades before 4 days,
        # check if enough weekdays remain in the window to fill min days
        if target_reached and not locked and len(trading_days) < 4:
            days_needed = 4 - len(trading_days)
            remaining_days = 0
            check_date = target_reached_day + timedelta(days=1)
            while check_date < phase_end.date() and remaining_days < days_needed:
                if check_date.weekday() < 5:  # Mon-Fri
                    remaining_days += 1
                check_date += timedelta(days=1)
            if remaining_days >= days_needed:
                locked = True
                trading_days_total = len(trading_days) + days_needed
                days_used = (check_date - phase_start.date()).days

        profit_pct = (equity - starting_equity) / initial * 100
        if max_dd >= 0.10:
            outcome = "FAIL_DD"
        elif max_daily_dd >= 0.05:
            outcome = "FAIL_DAILY_DD"
        elif locked or (equity >= target_equity and len(trading_days) >= 4):
            outcome = "PASS"
        elif equity >= target_equity:
            # Target reached but not enough weekdays left in window (very rare)
            outcome = "FAIL_MIN_DAYS"
        else:
            outcome = "FAIL_PROFIT"

        return {"outcome": outcome, "profit_pct": round(profit_pct, 2),
                "final_equity": round(equity, 2),
                "max_dd": round(max_dd * 100, 2),
                "max_daily_dd": round(max_daily_dd * 100, 2),
                "trading_days": len(trading_days), "days_used": days_used}

    p1 = run_phase(start_date, p1_days, 10.0, initial, risk_factor=1.0)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0, initial, risk_factor=p2_risk_factor)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def simulate_funded(streams, deck_combos, fund_start,
                    risk_factor=1.0, daily_cap=2.5, cooldown=1,
                    max_instr=99, max_losses=99, months=18):
    """Simulate a funded account with given risk controls."""
    fund_end = fund_start + timedelta(days=months * 30)
    initial = 100000
    equity = initial
    daily_start_eq = initial
    current_day = None
    combo_day_losses = defaultdict(int)
    instr_day_trades_d = defaultdict(int)
    total_daily_losses_d = 0
    daily_stopped = False
    max_dd = 0
    max_daily_dd = 0
    monthly_pnl = defaultdict(float)
    terminated = False
    termination_day = None

    all_trades = []
    for combo in deck_combos:
        if combo not in streams:
            continue
        for t in streams[combo]:
            if fund_start.date() <= t["date"] < fund_end.date():
                all_trades.append(t)
    all_trades.sort(key=lambda x: x["ts"])

    for t in all_trades:
        if terminated:
            break

        if t["date"] != current_day:
            if current_day is not None:
                dd = (daily_start_eq - equity) / initial
                max_daily_dd = max(max_daily_dd, dd)
            current_day = t["date"]
            daily_start_eq = equity
            combo_day_losses.clear()
            instr_day_trades_d.clear()
            total_daily_losses_d = 0
            daily_stopped = False

        if daily_stopped:
            continue

        combo = t["combo"]
        if combo_day_losses[combo] >= cooldown:
            continue

        instrument = ALL_COMBOS[combo]["symbol"]
        if instr_day_trades_d[instrument] >= max_instr:
            continue
        if total_daily_losses_d >= max_losses:
            continue

        pnl = t["pnl"] * risk_factor
        equity += pnl
        instr_day_trades_d[instrument] += 1
        month_key = t["date"].strftime("%Y-%m")
        monthly_pnl[month_key] += pnl

        if pnl < 0:
            combo_day_losses[combo] += 1
            total_daily_losses_d += 1

        daily_loss_pct = (daily_start_eq - equity) / initial * 100
        if daily_loss_pct >= daily_cap:
            daily_stopped = True

        dd_total = (initial - equity) / initial
        max_dd = max(max_dd, dd_total)
        daily_dd_hard = (daily_start_eq - equity) / initial

        if dd_total >= 0.10 or daily_dd_hard >= 0.05:
            terminated = True
            termination_day = t["date"]

    n_months = len(monthly_pnl)
    total_profit = equity - initial
    avg_monthly = total_profit / max(n_months, 1)
    win_months = sum(1 for v in monthly_pnl.values() if v > 0)

    return {
        "months": n_months, "terminated": terminated,
        "term_day": termination_day,
        "total_pnl": total_profit, "avg_monthly": avg_monthly,
        "win_months": win_months, "max_dd": max_dd * 100,
        "max_daily_dd": max_daily_dd * 100,
    }


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in DECK})

    data_dict = {}
    print("Loading data...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2026-01-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (-> {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    streams = precompute_trades(config, instruments, data_dict, DECK)

    print(f"\n{'='*120}")
    print(f"  PRODUCTION VALIDATION — fixed config, no optimization")
    print(f"  Deck: {len(DECK)} combos (Decorr16_A)")
    print(f"  Exam:   DC{EXAM_CONFIG['daily_cap']} CD{EXAM_CONFIG['cooldown']} "
          f"P2x{EXAM_CONFIG['p2_rf']} MI{EXAM_CONFIG['max_instr']} ML{EXAM_CONFIG['max_losses']}")
    print(f"  Funded: RF{FUNDED_CONFIG['risk_factor']} DC{FUNDED_CONFIG['daily_cap']} "
          f"CD{FUNDED_CONFIG['cooldown']} MI{FUNDED_CONFIG['max_instr']} ML{FUNDED_CONFIG['max_losses']}")
    print(f"{'='*120}")

    # ══════════════════════════════════════════════════════════════
    # PHASE 1: EXAM VALIDATION — every 30 days, 2016-2025
    # ══════════════════════════════════════════════════════════════

    last_data_date = min(data_dict[sym].index[-1] for sym in data_dict)
    max_oos_start = last_data_date - timedelta(days=90)
    is_windows = pd.date_range("2016-01-01", "2024-10-01", freq="30D")
    oos_windows = pd.date_range("2025-01-01", max_oos_start, freq="30D")

    print(f"\n  Exam windows: {len(is_windows)} IS + {len(oos_windows)} OOS")

    # Year-by-year
    print(f"\n{'='*120}")
    print(f"  YEAR-BY-YEAR EXAM RESULTS")
    print(f"{'='*120}")
    print(f"  {'Year':<6s} {'Win':>4s} {'P1 Pass':>8s} {'P1%':>6s} {'Funded':>8s} "
          f"{'Fund%':>6s} {'DD Fail':>8s} {'DDD Fail':>9s} {'Prof Fail':>10s} {'Type':>4s}")
    print(f"  {'-'*85}")

    total_is_funded = 0
    total_is_n = 0
    total_oos_funded = 0
    total_oos_n = 0

    for year in range(2016, 2026):
        year_windows = pd.date_range(f"{year}-01-01", f"{year}-10-01", freq="30D")
        if year == 2025:
            year_windows = oos_windows  # use truncated windows

        funded = p1_pass = dd_fails = ddd_fails = prof_fails = 0

        for start in year_windows:
            r = simulate_exam(
                streams, DECK, start,
                daily_loss_cap_pct=EXAM_CONFIG["daily_cap"],
                combo_daily_max_losses=EXAM_CONFIG["cooldown"],
                p2_risk_factor=EXAM_CONFIG["p2_rf"],
                max_instr_per_day=EXAM_CONFIG["max_instr"],
                max_daily_losses=EXAM_CONFIG["max_losses"],
            )
            if r["exam"] == "FUNDED": funded += 1
            if r["p1"]["outcome"] == "PASS": p1_pass += 1
            if r["p1"]["outcome"] == "FAIL_DD": dd_fails += 1
            if r["p1"]["outcome"] == "FAIL_DAILY_DD": ddd_fails += 1
            if r["p1"]["outcome"] == "FAIL_PROFIT": prof_fails += 1

        n = len(year_windows)
        tag = "OOS" if year >= 2025 else "IS"
        mark = " <<<" if year >= 2025 else ""

        if year < 2025:
            total_is_funded += funded
            total_is_n += n
        else:
            total_oos_funded += funded
            total_oos_n += n

        print(f"  {year:<6d} {n:>4d} {p1_pass:>3d}/{n:<4d} {p1_pass/n*100:>5.1f}% "
              f"{funded:>3d}/{n:<4d} {funded/n*100:>5.1f}% "
              f"{dd_fails:>8d} {ddd_fails:>9d} {prof_fails:>10d} {tag:>4s}{mark}")

    is_rate = total_is_funded / total_is_n * 100 if total_is_n else 0
    oos_rate = total_oos_funded / total_oos_n * 100 if total_oos_n else 0

    print(f"\n  TOTAL IS:  {total_is_funded}/{total_is_n} = {is_rate:.1f}%")
    print(f"  TOTAL OOS: {total_oos_funded}/{total_oos_n} = {oos_rate:.1f}%")
    print(f"  Gap: {is_rate - oos_rate:+.1f}pp")

    # Detailed OOS breakdown
    print(f"\n{'='*120}")
    print(f"  2025 OOS — DETAILED BREAKDOWN")
    print(f"{'='*120}")
    print(f"  {'Window':<12s} {'P1':>14s} {'P1 Prof':>8s} {'P1 DD':>6s} "
          f"{'P2':>14s} {'P2 Prof':>8s} {'Exam':>10s}")
    print(f"  {'-'*80}")

    for start in oos_windows:
        r = simulate_exam(
            streams, DECK, start,
            daily_loss_cap_pct=EXAM_CONFIG["daily_cap"],
            combo_daily_max_losses=EXAM_CONFIG["cooldown"],
            p2_risk_factor=EXAM_CONFIG["p2_rf"],
            max_instr_per_day=EXAM_CONFIG["max_instr"],
            max_daily_losses=EXAM_CONFIG["max_losses"],
        )
        p1 = r["p1"]
        p2_out = r["p2"]["outcome"] if r["p2"] else "-"
        p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
        marker = " <<<" if r["exam"] == "FUNDED" else ""
        print(f"  {str(start.date()):<12s} {p1['outcome']:>14s} {p1['profit_pct']:>+7.1f}% "
              f"{p1['max_dd']:>5.1f}% {p2_out:>14s} {p2_prof:>8s} {r['exam']:>10s}{marker}")

    # ══════════════════════════════════════════════════════════════
    # PHASE 2: FUNDED SURVIVAL
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  FUNDED ACCOUNT SURVIVAL — RF{FUNDED_CONFIG['risk_factor']} "
          f"DC{FUNDED_CONFIG['daily_cap']} CD{FUNDED_CONFIG['cooldown']}")
    print(f"{'='*120}")

    funded_windows = pd.date_range("2016-01-01", "2024-07-01", freq="60D")
    survival_results = []

    for start in funded_windows:
        fund_start = start + timedelta(days=90)
        sr = simulate_funded(
            streams, DECK, fund_start,
            risk_factor=FUNDED_CONFIG["risk_factor"],
            daily_cap=FUNDED_CONFIG["daily_cap"],
            cooldown=FUNDED_CONFIG["cooldown"],
            max_instr=FUNDED_CONFIG["max_instr"],
            max_losses=FUNDED_CONFIG["max_losses"],
        )
        survival_results.append({"start": fund_start.date(), **sr})

    print(f"\n  {'Start':<12s} {'Months':>7s} {'Status':>16s} {'Total PnL':>12s} "
          f"{'$/mo':>10s} {'Win/Tot':>8s} {'Max DD':>7s} {'Max DDD':>8s}")
    print(f"  {'-'*90}")

    for sr in survival_results:
        status = f"TERM {sr['term_day']}" if sr["terminated"] else "ALIVE (18mo)"
        wm = f"{sr['win_months']}/{sr['months']}"
        print(f"  {str(sr['start']):<12s} {sr['months']:>7d} {status:>16s} "
              f"${sr['total_pnl']:>+10,.0f} ${sr['avg_monthly']:>+8,.0f} "
              f"{wm:>8s} {sr['max_dd']:>6.1f}% {sr['max_daily_dd']:>7.1f}%")

    avg_months = np.mean([s["months"] for s in survival_results])
    med_months = np.median([s["months"] for s in survival_results])
    avg_pnl_mo = np.mean([s["avg_monthly"] for s in survival_results])
    term_rate = sum(1 for s in survival_results if s["terminated"]) / len(survival_results) * 100
    avg_total = np.mean([s["total_pnl"] for s in survival_results])

    print(f"\n  SUMMARY:")
    print(f"    Avg survival:   {avg_months:.1f} months (median: {med_months:.0f})")
    print(f"    Termination:    {term_rate:.0f}% within 18mo")
    print(f"    Avg monthly:    ${avg_pnl_mo:+,.0f} per $100K")
    print(f"    Avg total:      ${avg_total:+,.0f} lifetime per account")
    print(f"    Scaled $5K:     ${avg_pnl_mo * 0.05:+,.0f}/mo gross -> "
          f"${avg_pnl_mo * 0.05 * 0.8:+,.0f}/mo net (80% split)")

    # ══════════════════════════════════════════════════════════════
    # PHASE 3: COMBINED ROI
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  COMBINED ROI — Exam Factory + Funded Income")
    print(f"{'='*120}")

    exam_cost = 40  # EUR
    fund_rate = min(is_rate, oos_rate) / 100 if oos_rate > 0 else is_rate / 200
    monthly_income_5k = avg_pnl_mo * 0.05 * 0.8

    print(f"\n  Fund rate: {fund_rate*100:.1f}% | Avg survival: {avg_months:.1f}mo")
    print(f"  Monthly income per funded $5K: EUR{monthly_income_5k:+,.0f}")
    print(f"  Exam fee: EUR{exam_cost} (refunded on funding)")

    print(f"\n  {'Exams/mo':>9s} {'Cost':>7s} {'New/mo':>7s} {'Active':>7s} "
          f"{'Income':>9s} {'Net/mo':>9s} {'Net/yr':>10s}")
    print(f"  {'-'*70}")

    for n_ex in [5, 10, 15, 20, 30]:
        new_funded = n_ex * fund_rate
        cost = n_ex * exam_cost
        refund = new_funded * exam_cost
        net_cost = cost - refund
        active = new_funded * avg_months
        income = active * monthly_income_5k
        net = income - net_cost
        net_yr = net * 12
        print(f"  {n_ex:>9d} EUR{cost:>4.0f} {new_funded:>6.1f} {active:>6.1f} "
              f"EUR{income:>7.0f} EUR{net:>+7.0f} EUR{net_yr:>+8.0f}")

    # Worst case
    print(f"\n  WORST CASE (half fund rate, half survival, half income):")
    wc_rate = fund_rate / 2
    wc_surv = avg_months / 2
    wc_income = monthly_income_5k / 2
    for n_ex in [10, 20]:
        new_f = n_ex * wc_rate
        cost = n_ex * exam_cost
        refund = new_f * exam_cost
        active = new_f * wc_surv
        inc = active * wc_income
        net = inc - (cost - refund)
        print(f"    {n_ex} exams/mo -> {new_f:.1f} funded, {active:.1f} active -> "
              f"EUR{inc:.0f} income - EUR{cost - refund:.0f} cost = EUR{net:+.0f}/mo")

    # ══════════════════════════════════════════════════════════════
    # VERDICT
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  FINAL VERDICT")
    print(f"{'='*120}")

    print(f"\n  EXAM MODE:   DC{EXAM_CONFIG['daily_cap']} CD{EXAM_CONFIG['cooldown']} "
          f"P2x{EXAM_CONFIG['p2_rf']} MI{EXAM_CONFIG['max_instr']} ML{EXAM_CONFIG['max_losses']}")
    print(f"    -> {is_rate:.1f}% IS / {oos_rate:.1f}% OOS funded rate")

    print(f"\n  FUNDED MODE: RF{FUNDED_CONFIG['risk_factor']} DC{FUNDED_CONFIG['daily_cap']} "
          f"CD{FUNDED_CONFIG['cooldown']} MI{FUNDED_CONFIG['max_instr']} ML{FUNDED_CONFIG['max_losses']}")
    print(f"    -> {avg_months:.1f}mo avg survival, ${avg_pnl_mo * 0.05:+,.0f}/mo per $5K")
    print(f"    -> {term_rate:.0f}% terminated within 18mo")

    # Cross-check with optimize_deck.py
    # Note: IS rate may differ slightly because optimize_deck uses different window range
    # OOS rate is the real validation (same 2025 windows, same logic)
    print(f"\n  CROSS-CHECK vs optimize_deck.py:")
    expected_oos = 28.6
    oos_match = abs(oos_rate - expected_oos) < 1.0
    print(f"    OOS rate: {oos_rate:.1f}% (expected ~{expected_oos}%) {'OK' if oos_match else 'MISMATCH!'}")

    if oos_match:
        print(f"\n  VALIDATED — OOS results match optimizer. Ready for deployment.")
    else:
        print(f"\n  WARNING — OOS results differ from optimizer. Investigate before deploying!")


if __name__ == "__main__":
    main()
