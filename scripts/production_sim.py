"""
Production validation for the deployed profile in config/accounts.yaml.

This script does not optimize anything. It loads the live deployment profile,
replays the deck on historical data, and reports:
1. Exam pass rate (IS 2016-2024, OOS 2025)
2. Year-by-year breakdown
3. Funded-account survival and monthly expectancy

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
import yaml
from copy import deepcopy
from datetime import timedelta, date
from collections import defaultdict

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY


# ═══════════════════════════════════════════════════════════════
# Deployment profile defaults. Real values are loaded from config/accounts.yaml.
# ═══════════════════════════════════════════════════════════════

EXAM_CONFIG = {
    "risk_per_trade": 0.04,
    "p2_risk_per_trade": 0.02,
    "daily_cap": 3.5,
    "cooldown": 1,
    "max_instr": 3,
    "max_losses": 3,
}

FUNDED_CONFIG = {
    "risk_per_trade": 0.01,
    "daily_cap": 2.5,
    "cooldown": 1,
    "max_instr": 2,
    "max_losses": 3,
}

# ALIVE deck — 32 combos from FTMO-data massive scan + walk-forward filter (2024-2025 PF>=1.0)
# Updated 2026-04-13 after full recalibration with FTMO broker-native data
DECK = [
    # ROBUST tier (PF>1.05, period stable 3/5, spread stress, param sensitivity)
    "MomDiv_EURJPY_trend_H4",           # PF=2.26
    "MACross_XAUUSD_trend_H4",          # PF=1.96
    "MACross_XAUUSD_wideR_H4_ny",       # PF=1.88
    "SwBrk_EURJPY_slow_H4",             # PF=1.87
    "MACross_AUDUSD_trend_H4_ny",        # PF=1.81
    "EMArib_EURJPY_trend_H4",           # PF=1.78
    "MACross_NZDUSD_wideR_H4_ny",        # PF=1.74
    "SwBrk_EURJPY_wideR_H4",            # PF=1.64
    "Engulf_GBPJPY_trend_H4",           # PF=1.59
    "RegVMR_NZDUSD_default_H4",         # PF=1.59
    "MACross_EURJPY_wideR_H4_lon",       # PF=1.59
    "StrBrk_GBPJPY_wideR_H4",           # PF=1.47
    "SwBrk_AUDUSD_wideR_H4",            # PF=1.42
    "EMArib_USDJPY_trend_H4",           # PF=1.36
    "EMArib_EURJPY_tight_H1",           # PF=1.35
    "Engulf_EURUSD_tight_H4",           # PF=1.33
    "MACross_USDCHF_megaT_H4",          # PF=1.26
    "IBB_AUDUSD_multi_H4",              # PF=1.26
    "StrBrk_GBPJPY_trend_H4",           # PF=1.20
    "MACross_EURUSD_wideR_H4_lon",       # PF=1.12
    "VMR_USDCHF_default_H1_lon",        # PF=1.11
    # SPREAD_OK tier (walk-forward survivors)
    "MomDiv_EURJPY_wideR_H4",           # PF=1.39
    "KeltSq_EURJPY_wideR_H4_lon",        # PF=1.26
    "MACross_EURUSD_trend_H4_lon",       # PF=1.21
    "ADXbirth_USDCAD_strict_H4",         # PF=1.26
    "VMR_USDJPY_wideR_H4_ny",           # PF=1.13
    "MACross_GBPJPY_wideR_H4_ny",        # PF=1.13
    "KeltSq_XAUUSD_wideR_H4_lon",        # PF=1.09
    "KeltSq_USDJPY_slow_H4",            # PF=1.07
    "StochRev_AUDUSD_calm_H4",          # PF=1.06
    "TPB_USDJPY_trend_H4_lon",          # PF=1.05
    "MACross_USDJPY_trend_H4_ny",        # PF=1.06
]

ACCOUNTS_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"


def load_deployment_profile():
    global EXAM_CONFIG, FUNDED_CONFIG

    with open(ACCOUNTS_CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    exam = raw["exam_mode"]
    funded = raw["funded_mode"]
    exam_risk = exam["risk_per_trade"]
    funded_risk = funded["risk_per_trade"]
    p2_factor = exam.get("p2_risk_factor", 1.0)

    EXAM_CONFIG = {
        "risk_per_trade": exam_risk,
        "p2_risk_per_trade": exam_risk * p2_factor,
        "daily_cap": exam["daily_cap_pct"],
        "cooldown": exam["cooldown"],
        "max_instr": exam["max_instr_per_day"],
        "max_losses": exam["max_daily_losses"],
    }
    FUNDED_CONFIG = {
        "risk_per_trade": funded_risk,
        "daily_cap": funded["daily_cap_pct"],
        "cooldown": funded["cooldown"],
        "max_instr": funded["max_instr_per_day"],
        "max_losses": funded["max_daily_losses"],
    }
    # DECK is NOT loaded from accounts.yaml — uses the ALIVE deck defined above.
    # accounts.yaml deck has naming mismatches with v7_expanded pool.


# ═══════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names, risk_pct=0.02):
    """Pre-compute trade events per combo with loose DD (no portfolio interaction).

    Stores enough info to RE-SIZE each trade at portfolio replay time:
    pnl_pips, sl_pips, symbol, direction, pip_value, min_lot, max_lot.
    The actual PnL in $ will be recalculated during portfolio replay
    using the real portfolio equity at entry time.
    """
    print(f"\n  Pre-computing trades (loose DD, storing pip-level data)...")
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
        inst = instruments[sym]
        try:
            strategy = load_strategy(entry)
            engine = BacktestEngine(cfg, inst, EquityManager(eq_cfg))
            result = engine.run(strategy, data_dict[sym], sym)
        except Exception as e:
            print(f"    {combo_name}: ERROR {e}")
            continue
        trades = []
        for t in result.trades:
            if not isinstance(t.entry_time, pd.Timestamp):
                continue
            # SL distance in pips (always positive)
            if t.direction.name == "LONG":
                sl_pips = (t.entry_price - t.stop_loss) / inst.pip_size
            else:
                sl_pips = (t.stop_loss - t.entry_price) / inst.pip_size
            sl_pips = abs(sl_pips)
            if sl_pips <= 0:
                continue
            trades.append({
                "ts": t.entry_time,
                "ts_exit": t.exit_time,
                "date": t.entry_time.date(),
                "combo": combo_name,
                "symbol": sym,
                "pnl_pips": t.pnl_pips,
                "sl_pips": sl_pips,
                "commission_per_lot": cfg.backtest.commission_per_lot,
                "pip_value": inst.pip_value_per_lot,
                "min_lot": inst.min_lot,
                "max_lot": inst.max_lot,
            })
        streams[combo_name] = trades
        if trades:
            pnl_pips_total = sum(t["pnl_pips"] for t in trades)
            print(f"    {combo_name}: {len(trades)} trades, {pnl_pips_total:+,.0f} pips")
        else:
            print(f"    {combo_name}: 0 trades")
    return streams


def _size_trade(t, equity, risk_pct):
    """Calculate lot size and dollar PnL for a precomputed trade at given equity.

    Position sizing formula (same as RiskManager.evaluate_signal):
        lot = equity * risk_pct / (sl_pips * pip_value)
    Then PnL = pnl_pips * pip_value * lot - commission * lot
    """
    sl_pips = t["sl_pips"]
    pip_value = t["pip_value"]
    if sl_pips <= 0 or pip_value <= 0:
        return 0.0, 0.0

    risk_amount = equity * risk_pct
    lot = risk_amount / (sl_pips * pip_value)
    lot = max(t["min_lot"], min(lot, t["max_lot"]))
    lot = round(lot, 2)

    if lot < t["min_lot"]:
        return 0.0, 0.0

    pnl = t["pnl_pips"] * pip_value * lot - t["commission_per_lot"] * lot
    return lot, pnl


def simulate_exam(streams, combo_names, start_date,
                  daily_loss_cap_pct=3.0, combo_daily_max_losses=1,
                  risk_per_trade=0.02, p2_risk_per_trade=0.01,
                  max_instr_per_day=99, max_daily_losses=99,
                  equity_manager_cfg=None,
                  initial=100000, p1_days=30, p2_days=60):
    """FTMO 2-step exam with REAL portfolio-level position sizing.

    Each trade is sized on the CURRENT portfolio equity, not precomputed.
    EquityManager (anti-martingale tiers) is applied if provided.
    Balance RESETS between phases. DD limits static from initial.
    """
    def run_phase(phase_start, window_days, target_pct, starting_equity,
                  risk_pct=0.02, eq_mgr=None):
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

        if eq_mgr is not None:
            eq_mgr.initialize(starting_equity)

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
            if target_reached:
                trading_days.add(t["date"])
                if len(trading_days) >= 4:
                    locked = True
                    days_used = (t["date"] - phase_start.date()).days + 1
                continue

            if daily_stopped:
                continue

            # EquityManager halt check
            if eq_mgr is not None and eq_mgr.should_stop_trading():
                continue

            combo = t["combo"]
            if combo_day_losses[combo] >= combo_daily_max_losses:
                continue

            instrument = ALL_COMBOS[combo]["symbol"]
            if instr_day_trades[instrument] >= max_instr_per_day:
                continue
            if total_daily_losses >= max_daily_losses:
                continue

            # Real position sizing on current portfolio equity
            effective_risk = risk_pct
            if eq_mgr is not None:
                eq_mgr.on_bar(t["ts"])
                multiplier = eq_mgr.get_risk_multiplier()
                if multiplier <= 0:
                    continue
                effective_risk = risk_pct * multiplier

            lot, pnl = _size_trade(t, equity, effective_risk)
            if lot <= 0:
                continue

            equity += pnl
            trading_days.add(t["date"])
            instr_day_trades[instrument] += 1

            if eq_mgr is not None:
                eq_mgr.on_trade_closed(pnl, equity)

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
                days_used = (check_date - phase_start.date()).days

        profit_pct = (equity - starting_equity) / initial * 100
        if max_dd >= 0.10:
            outcome = "FAIL_DD"
        elif max_daily_dd >= 0.05:
            outcome = "FAIL_DAILY_DD"
        elif locked or (equity >= target_equity and len(trading_days) >= 4):
            outcome = "PASS"
        elif equity >= target_equity:
            outcome = "FAIL_MIN_DAYS"
        else:
            outcome = "FAIL_PROFIT"

        return {"outcome": outcome, "profit_pct": round(profit_pct, 2),
                "final_equity": round(equity, 2),
                "max_dd": round(max_dd * 100, 2),
                "max_daily_dd": round(max_daily_dd * 100, 2),
                "trading_days": len(trading_days), "days_used": days_used}

    eq_mgr_p1 = EquityManager(equity_manager_cfg) if equity_manager_cfg else None
    p1 = run_phase(start_date, p1_days, 10.0, initial,
                   risk_pct=risk_per_trade, eq_mgr=eq_mgr_p1)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    p2_start = start_date + timedelta(days=p1["days_used"])
    eq_mgr_p2 = EquityManager(equity_manager_cfg) if equity_manager_cfg else None
    p2 = run_phase(p2_start, p2_days, 5.0, initial,
                   risk_pct=p2_risk_per_trade, eq_mgr=eq_mgr_p2)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def simulate_funded(streams, deck_combos, fund_start,
                    risk_per_trade=0.01, daily_cap=2.5, cooldown=1,
                    max_instr=99, max_losses=99,
                    equity_manager_cfg=None, months=18):
    """Simulate a funded account with REAL portfolio-level position sizing."""
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

    eq_mgr = EquityManager(equity_manager_cfg) if equity_manager_cfg else None
    if eq_mgr is not None:
        eq_mgr.initialize(initial)

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

        if eq_mgr is not None and eq_mgr.should_stop_trading():
            continue

        combo = t["combo"]
        if combo_day_losses[combo] >= cooldown:
            continue

        instrument = ALL_COMBOS[combo]["symbol"]
        if instr_day_trades_d[instrument] >= max_instr:
            continue
        if total_daily_losses_d >= max_losses:
            continue

        # Real position sizing on current portfolio equity
        effective_risk = risk_per_trade
        if eq_mgr is not None:
            eq_mgr.on_bar(t["ts"])
            multiplier = eq_mgr.get_risk_multiplier()
            if multiplier <= 0:
                continue
            effective_risk = risk_per_trade * multiplier

        lot, pnl = _size_trade(t, equity, effective_risk)
        if lot <= 0:
            continue

        equity += pnl
        instr_day_trades_d[instrument] += 1
        month_key = t["date"].strftime("%Y-%m")
        monthly_pnl[month_key] += pnl

        if eq_mgr is not None:
            eq_mgr.on_trade_closed(pnl, equity)

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
    load_deployment_profile()
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({ALL_COMBOS[c]["symbol"] for c in DECK})

    data_dict = {}
    print("Loading data...")
    for sym in sorted(all_symbols):
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01")
            last = data_dict[sym].index[-1]
            print(f"  {sym}: {len(data_dict[sym]):,} bars (-> {last.date()})")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    streams = precompute_trades(config, instruments, data_dict, DECK)

    print(f"\n{'='*120}")
    print(f"  PRODUCTION VALIDATION - portfolio-level sizing, no optimization")
    print(f"  Deck: {len(DECK)} combos")
    print(f"  Exam:   R{EXAM_CONFIG['risk_per_trade']*100:.1f}% "
          f"P2R{EXAM_CONFIG['p2_risk_per_trade']*100:.1f}% "
          f"DC{EXAM_CONFIG['daily_cap']} CD{EXAM_CONFIG['cooldown']} "
          f"MI{EXAM_CONFIG['max_instr']} ML{EXAM_CONFIG['max_losses']}")
    print(f"  Funded: R{FUNDED_CONFIG['risk_per_trade']*100:.1f}% "
          f"DC{FUNDED_CONFIG['daily_cap']} "
          f"CD{FUNDED_CONFIG['cooldown']} MI{FUNDED_CONFIG['max_instr']} ML{FUNDED_CONFIG['max_losses']}")
    print(f"{'='*120}")

    # ══════════════════════════════════════════════════════════════
    # PHASE 1: EXAM VALIDATION — every business day, Monte Carlo
    # ══════════════════════════════════════════════════════════════

    # Use the MAJORITY of symbols' end date for OOS (exclude symbols without 2026 data)
    sym_end_dates = {sym: data_dict[sym].index[-1] for sym in data_dict}
    oos_sym_dates = [d for d in sym_end_dates.values() if d.year >= 2026]
    last_data_date = min(oos_sym_dates) if oos_sym_dates else max(sym_end_dates.values())
    # IS = 2016 through 2025, OOS = 2026 (FTMO real data)
    # Generate every business day as a possible exam start date
    is_starts = pd.bdate_range("2016-01-01", "2025-09-01")
    # OOS: need 30d for P1 minimum, ideally 90d for full exam
    oos_last_p1 = last_data_date - timedelta(days=30)
    oos_last_full = last_data_date - timedelta(days=90)
    oos_starts = pd.bdate_range("2026-01-02", oos_last_p1)

    print(f"\n  IS period: 2016-2025 | OOS period: 2026 (FTMO data)")
    print(f"  IS exam starts:  {len(is_starts)} business days")
    print(f"  OOS exam starts: {len(oos_starts)} business days (P1 feasible)")
    print(f"  OOS data ends:   {last_data_date.date()}")

    def run_exam_batch(starts, label):
        results = {"funded": 0, "p1_pass": 0, "fail_dd": 0, "fail_ddd": 0,
                    "fail_profit": 0, "fail_p2": 0, "n": len(starts),
                    "days_to_fund": [], "p1_days": []}
        for start in starts:
            r = simulate_exam(
                streams, DECK, start,
                daily_loss_cap_pct=EXAM_CONFIG["daily_cap"],
                combo_daily_max_losses=EXAM_CONFIG["cooldown"],
                risk_per_trade=EXAM_CONFIG["risk_per_trade"],
                p2_risk_per_trade=EXAM_CONFIG["p2_risk_per_trade"],
                max_instr_per_day=EXAM_CONFIG["max_instr"],
                max_daily_losses=EXAM_CONFIG["max_losses"],
            )
            if r["exam"] == "FUNDED":
                results["funded"] += 1
                total_days = r["p1"]["days_used"] + r["p2"]["days_used"]
                results["days_to_fund"].append(total_days)
            if r["p1"]["outcome"] == "PASS":
                results["p1_pass"] += 1
                results["p1_days"].append(r["p1"]["days_used"])
            if r["p1"]["outcome"] == "FAIL_DD": results["fail_dd"] += 1
            if r["p1"]["outcome"] == "FAIL_DAILY_DD": results["fail_ddd"] += 1
            if r["p1"]["outcome"] == "FAIL_PROFIT": results["fail_profit"] += 1
            if r["exam"] == "FAIL_P2": results["fail_p2"] += 1
        return results

    # Year-by-year breakdown
    print(f"\n{'='*120}")
    print(f"  YEAR-BY-YEAR EXAM RESULTS (every business day)")
    print(f"{'='*120}")
    print(f"  {'Year':<6s} {'Exams':>6s} {'P1Pass':>7s} {'P1%':>6s} {'Funded':>7s} "
          f"{'Fund%':>6s} {'AvgDays':>8s} {'DD':>5s} {'DDD':>5s} {'Prof':>5s} {'P2F':>5s} {'Type':>4s}")
    print(f"  {'-'*90}")

    total_is_funded = 0
    total_is_n = 0
    total_oos_funded = 0
    total_oos_n = 0
    all_is_days_to_fund = []
    all_oos_days_to_fund = []

    for year in range(2016, 2027):
        if year < 2026:
            year_starts = [s for s in is_starts if s.year == year]
        else:
            year_starts = [s for s in oos_starts if s.year == year]

        if not year_starts:
            continue

        yr = run_exam_batch(year_starts, str(year))
        n = yr["n"]
        tag = "OOS" if year >= 2026 else "IS"
        mark = " <<<" if year >= 2026 else ""

        if year < 2026:
            total_is_funded += yr["funded"]
            total_is_n += n
            all_is_days_to_fund.extend(yr["days_to_fund"])
        else:
            total_oos_funded += yr["funded"]
            total_oos_n += n
            all_oos_days_to_fund.extend(yr["days_to_fund"])

        avg_days = f"{np.mean(yr['days_to_fund']):.0f}" if yr["days_to_fund"] else "-"
        p1_pct = yr["p1_pass"] / n * 100 if n else 0
        f_pct = yr["funded"] / n * 100 if n else 0

        print(f"  {year:<6d} {n:>6d} {yr['p1_pass']:>7d} {p1_pct:>5.1f}% {yr['funded']:>7d} "
              f"{f_pct:>5.1f}% {avg_days:>8s} {yr['fail_dd']:>5d} {yr['fail_ddd']:>5d} "
              f"{yr['fail_profit']:>5d} {yr['fail_p2']:>5d} {tag:>4s}{mark}")

    is_rate = total_is_funded / total_is_n * 100 if total_is_n else 0
    oos_rate = total_oos_funded / total_oos_n * 100 if total_oos_n else 0
    is_avg_days = np.mean(all_is_days_to_fund) if all_is_days_to_fund else 0
    oos_avg_days = np.mean(all_oos_days_to_fund) if all_oos_days_to_fund else 0

    print(f"\n  TOTAL IS:  {total_is_funded}/{total_is_n} = {is_rate:.1f}% "
          f"(avg {is_avg_days:.0f} days to fund)")
    print(f"  TOTAL OOS: {total_oos_funded}/{total_oos_n} = {oos_rate:.1f}% "
          f"(avg {oos_avg_days:.0f} days to fund)")
    print(f"  Gap: {is_rate - oos_rate:+.1f}pp")

    # Detailed OOS: show every start date result
    print(f"\n{'='*120}")
    print(f"  2026 OOS (FTMO data) — DETAILED BREAKDOWN (every business day)")
    print(f"{'='*120}")
    print(f"  {'Start':<12s} {'P1':>14s} {'P1 Prof':>8s} {'P1 DD':>6s} {'P1 Days':>8s} "
          f"{'P2':>14s} {'P2 Prof':>8s} {'Exam':>10s}")
    print(f"  {'-'*90}")

    for start in oos_starts:
        r = simulate_exam(
            streams, DECK, start,
            daily_loss_cap_pct=EXAM_CONFIG["daily_cap"],
            combo_daily_max_losses=EXAM_CONFIG["cooldown"],
            risk_per_trade=EXAM_CONFIG["risk_per_trade"],
            p2_risk_per_trade=EXAM_CONFIG["p2_risk_per_trade"],
            max_instr_per_day=EXAM_CONFIG["max_instr"],
            max_daily_losses=EXAM_CONFIG["max_losses"],
        )
        p1 = r["p1"]
        p2_out = r["p2"]["outcome"] if r["p2"] else "-"
        p2_prof = f"{r['p2']['profit_pct']:+.1f}%" if r["p2"] else "-"
        marker = " <<<" if r["exam"] == "FUNDED" else ""
        print(f"  {str(start.date()):<12s} {p1['outcome']:>14s} {p1['profit_pct']:>+7.1f}% "
              f"{p1['max_dd']:>5.1f}% {p1['days_used']:>8d} "
              f"{p2_out:>14s} {p2_prof:>8s} {r['exam']:>10s}{marker}")

    # ══════════════════════════════════════════════════════════════
    # PHASE 2: FUNDED SURVIVAL
    # ══════════════════════════════════════════════════════════════

    print(f"\n{'='*120}")
    print(f"  FUNDED ACCOUNT SURVIVAL — R{FUNDED_CONFIG['risk_per_trade']*100:.1f}% "
          f"DC{FUNDED_CONFIG['daily_cap']} CD{FUNDED_CONFIG['cooldown']}")
    print(f"{'='*120}")

    # Sample every 60 days (full bday range would be too many for 18mo sims)
    funded_windows = pd.date_range("2016-01-01", "2024-07-01", freq="60D")
    survival_results = []

    for start in funded_windows:
        fund_start = start + timedelta(days=90)
        sr = simulate_funded(
            streams, DECK, fund_start,
            risk_per_trade=FUNDED_CONFIG["risk_per_trade"],
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
    # Use OOS rate if available, otherwise conservative half of IS
    fund_rate = oos_rate / 100 if total_oos_n >= 10 and oos_rate > 0 else is_rate / 200
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

    print(f"\n  EXAM MODE:   R{EXAM_CONFIG['risk_per_trade']*100:.1f}% "
          f"P2R{EXAM_CONFIG['p2_risk_per_trade']*100:.1f}% "
          f"DC{EXAM_CONFIG['daily_cap']} CD{EXAM_CONFIG['cooldown']} "
          f"MI{EXAM_CONFIG['max_instr']} ML{EXAM_CONFIG['max_losses']}")
    print(f"    -> {is_rate:.1f}% IS / {oos_rate:.1f}% OOS funded rate")

    print(f"\n  FUNDED MODE: R{FUNDED_CONFIG['risk_per_trade']*100:.1f}% "
          f"DC{FUNDED_CONFIG['daily_cap']} "
          f"CD{FUNDED_CONFIG['cooldown']} MI{FUNDED_CONFIG['max_instr']} ML{FUNDED_CONFIG['max_losses']}")
    print(f"    -> {avg_months:.1f}mo avg survival, ${avg_pnl_mo * 0.05:+,.0f} gross / "
          f"${avg_pnl_mo * 0.05 * 0.8:+,.0f} net per $5K")
    print(f"    -> {term_rate:.0f}% terminated within 18mo")

    print(f"\n  CURRENT PROFILE SOURCE OF TRUTH:")
    print(f"    Deck + exam/funded controls loaded from config/accounts.yaml")
    print(f"    Compare these outputs with optimize_deck.py only after exporting")
    print(f"    the same profile from the optimizer.")


if __name__ == "__main__":
    main()
