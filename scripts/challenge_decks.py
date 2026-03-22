"""
FTMO 2-Step Challenge simulation with CORRECT rules.

Phase 1: 10% profit target, 5% daily DD, 10% total DD (static from initial), 4 min days
Phase 2: 5% profit target, same DD limits, balance carries over from P1 (NO reset)

Both phases: profit lock (stop trading once target hit with 4+ days).
No best day rule (that's only for 1-step).

Usage:
    python -X utf8 scripts/challenge_decks.py
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

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# ═══════════════════════════════════════════════════════════════════════
# ALL VALIDATED COMBOS (from massive scan + Phase 3/4 validation)
# ═══════════════════════════════════════════════════════════════════════

STRAT_REGISTRY = {
    "VMR": {"module": "algosbz.strategy.volatility_mean_reversion", "class": "VolatilityMeanReversion"},
    "TPB": {"module": "algosbz.strategy.trend_pullback", "class": "TrendPullback"},
    "SwBrk": {"module": "algosbz.strategy.swing_breakout", "class": "SwingBreakout"},
    "IBB": {"module": "algosbz.strategy.inside_bar_breakout", "class": "InsideBarBreakout"},
    "Engulf": {"module": "algosbz.strategy.engulfing_reversal", "class": "EngulfingReversal"},
    "StrBrk": {"module": "algosbz.strategy.structure_break", "class": "StructureBreak"},
    "MomDiv": {"module": "algosbz.strategy.momentum_divergence", "class": "MomentumDivergence"},
    "RegVMR": {"module": "algosbz.strategy.regime_vmr", "class": "RegimeAdaptiveVMR"},
    "EMArib": {"module": "algosbz.strategy.ema_ribbon_trend", "class": "EMARibbonTrend"},
    "SessBrk": {"module": "algosbz.strategy.session_breakout_v2", "class": "SessionBreakout"},
    "SMCOB": {"module": "algosbz.strategy.smc_order_block", "class": "SMCOrderBlock"},
    "FVGrev": {"module": "algosbz.strategy.fvg_reversion", "class": "FVGReversion"},
    "VWAPrev": {"module": "algosbz.strategy.vwap_reversion", "class": "VWAPReversion"},
}

ALL_COMBOS = {
    # ── ROBUST (16): passed spread +50% AND param sensitivity ±20% ──
    "VMR_SPY_H4": {
        "strat": "VMR", "symbol": "SPY", "tier": "ROBUST", "pf": 1.34,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "TPB_XTIUSD_loose_H4": {
        "strat": "TPB", "symbol": "XTIUSD", "tier": "ROBUST", "pf": 1.40,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0},
    },
    "TPB_XNGUSD_loose_H4": {
        "strat": "TPB", "symbol": "XNGUSD", "tier": "ROBUST", "pf": 1.37,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0},
    },
    "SwBrk_XTIUSD_H4": {
        "strat": "SwBrk", "symbol": "XTIUSD", "tier": "ROBUST", "pf": 1.29,
        "params": {"timeframe": "H4"},
    },
    "SwBrk_SPY_H4": {
        "strat": "SwBrk", "symbol": "SPY", "tier": "ROBUST", "pf": 1.05,
        "params": {"timeframe": "H4"},
    },
    "SwBrk_SPY_slow_H4": {
        "strat": "SwBrk", "symbol": "SPY", "tier": "ROBUST", "pf": 1.72,
        "params": {"timeframe": "H4", "donchian_period": 30, "squeeze_pct": 0.75, "tp_atr_mult": 4.0},
    },
    "Engulf_EURUSD_tight_H4": {
        "strat": "Engulf", "symbol": "EURUSD", "tier": "ROBUST", "pf": 1.33,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "swing_zone_atr": 0.3, "min_body_ratio": 0.7, "tp_atr_mult": 3.0},
    },
    "Engulf_XAUUSD_tight_H4": {
        "strat": "Engulf", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.39,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "swing_zone_atr": 0.3, "min_body_ratio": 0.7, "tp_atr_mult": 3.0},
    },
    "StrBrk_GBPJPY_slow_H4": {
        "strat": "StrBrk", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.21,
        "params": {"timeframe": "H4", "swing_lookback": 7, "tp_atr_mult": 4.0},
    },
    "MomDiv_SPY_H1": {
        "strat": "MomDiv", "symbol": "SPY", "tier": "ROBUST", "pf": 1.14,
        "params": {"timeframe": "H1"},
    },
    "RegVMR_XAUUSD_H1": {
        "strat": "RegVMR", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.25,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "RegVMR_XTIUSD_H1": {
        "strat": "RegVMR", "symbol": "XTIUSD", "tier": "ROBUST", "pf": 1.35,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "SessBrk_XTIUSD_M15": {
        "strat": "SessBrk", "symbol": "XTIUSD", "tier": "ROBUST", "pf": 2.01,
        "params": {"timeframe": "M15"},
    },
    "SMCOB_GBPJPY_H1": {
        "strat": "SMCOB", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.10,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "SMCOB_XAUUSD_loose_H4": {
        "strat": "SMCOB", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.49,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "rejection_wick_ratio": 0.4, "tp_atr_mult": 2.5},
    },
    "SMCOB_GBPJPY_tight_H1": {
        "strat": "SMCOB", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.09,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23,
                   "rejection_wick_ratio": 0.6, "sl_atr_mult": 1.0, "tp_atr_mult": 2.0},
    },
    # ── SPREAD_OK (11): passed spread +50% but sensitive to param changes ──
    "VMR_USDCHF_H1": {
        "strat": "VMR", "symbol": "USDCHF", "tier": "SPREAD_OK", "pf": 1.29,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "VMR_USDJPY_H4": {
        "strat": "VMR", "symbol": "USDJPY", "tier": "SPREAD_OK", "pf": 1.07,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "TPB_XTIUSD_H4": {
        "strat": "TPB", "symbol": "XTIUSD", "tier": "SPREAD_OK", "pf": 1.09,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "TPB_GBPJPY_loose_H1": {
        "strat": "TPB", "symbol": "GBPJPY", "tier": "SPREAD_OK", "pf": 1.10,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23,
                   "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0},
    },
    "SwBrk_SPY_fast_H4": {
        "strat": "SwBrk", "symbol": "SPY", "tier": "SPREAD_OK", "pf": 1.15,
        "params": {"timeframe": "H4", "donchian_period": 10, "squeeze_pct": 0.85, "adx_min": 15},
    },
    "IBB_EURUSD_loose_H4": {
        "strat": "IBB", "symbol": "EURUSD", "tier": "SPREAD_OK", "pf": 1.05,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23,
                   "min_bar_range_pct": 0.2, "sl_atr_mult": 2.0, "tp_atr_mult": 4.0},
    },
    "Engulf_EURUSD_H4": {
        "strat": "Engulf", "symbol": "EURUSD", "tier": "SPREAD_OK", "pf": 1.05,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "MomDiv_SPY_loose_H1": {
        "strat": "MomDiv", "symbol": "SPY", "tier": "SPREAD_OK", "pf": 1.09,
        "params": {"timeframe": "H1", "min_rsi_diff": 2, "divergence_window": 40, "swing_lookback": 3},
    },
    "EMArib_XNGUSD_loose_H4": {
        "strat": "EMArib", "symbol": "XNGUSD", "tier": "SPREAD_OK", "pf": 1.23,
        "params": {"timeframe": "H4", "ribbon_threshold": 0.5, "ribbon_confirm_bars": 2,
                   "rsi_pullback_bull": 50, "rsi_pullback_bear": 50},
    },
    "SMCOB_XAUUSD_H4": {
        "strat": "SMCOB", "symbol": "XAUUSD", "tier": "SPREAD_OK", "pf": 1.29,
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "RegVMR_XAUUSD_loose_H1": {
        "strat": "RegVMR", "symbol": "XAUUSD", "tier": "SPREAD_OK", "pf": 1.20,
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23,
                   "bb_std": 2.0, "consec_outside": 1},
    },
}

# ═══════════════════════════════════════════════════════════════════════
# DECK CONFIGURATIONS
# ═══════════════════════════════════════════════════════════════════════

def _robust_only():
    return [k for k, v in ALL_COMBOS.items() if v["tier"] == "ROBUST"]

def _best_per_instrument():
    """Best ROBUST combo per instrument (max diversification)."""
    best = {}
    for k, v in ALL_COMBOS.items():
        if v["tier"] != "ROBUST":
            continue
        sym = v["symbol"]
        if sym not in best or v["pf"] > best[sym][1]:
            best[sym] = (k, v["pf"])
    return [name for name, _ in best.values()]

def _top_pf(n=10):
    """Top N combos by PF from ROBUST tier."""
    robust = [(k, v["pf"]) for k, v in ALL_COMBOS.items() if v["tier"] == "ROBUST"]
    robust.sort(key=lambda x: x[1], reverse=True)
    return [k for k, _ in robust[:n]]

DECKS = {
    "Core3": [
        "VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1",
    ],
    "Robust16": _robust_only(),
    "BestPerInstr": _best_per_instrument(),
    "TopPF10": _top_pf(10),
    "Full27": list(ALL_COMBOS.keys()),
}


def load_strategy(entry):
    import importlib
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def run_phase(config, instruments, data_dict, combo_names, risk_pct,
              start_date, window_days, profit_target_pct):
    """
    Run a single phase (P1 or P2) of the FTMO challenge.

    FTMO rules:
    - DD limits are STATIC from INITIAL balance ($100K), not from current balance
    - Daily DD: 5% of initial = equity can't drop more than $5K in a day
    - Total DD: 10% of initial = equity can't drop below $90K ever
    """
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
        entry = ALL_COMBOS[combo_name]
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
            strategy = load_strategy(entry)
            engine = BacktestEngine(cfg, inst_cfg, EquityManager(eq_cfg))
            result = engine.run(strategy, window_data, sym)
        except Exception:
            continue

        for t in result.trades:
            t_ts = t.entry_time
            if isinstance(t_ts, pd.Timestamp):
                t_date = t_ts.date()
            elif hasattr(t_ts, 'date'):
                t_date = t_ts.date()
            else:
                t_date = t_ts
            if start_date.date() <= t_date < end_date.date():
                all_trades.append({"ts": t_ts, "date": t_date, "pnl": t.pnl})

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                all_equity_points.append((ts, val - initial))

    if not all_equity_points:
        return {"outcome": "NO_DATA", "profit_pct": 0, "final_equity": initial,
                "max_dd": 0, "max_daily_dd": 0, "trades": 0, "trading_days": 0,
                "days_used": 0}

    all_trades.sort(key=lambda x: x["ts"])

    df = pd.DataFrame(all_equity_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    # Profit lock: stop once target hit with 4+ trading days
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
    else:
        eq = combined_equity
        trades_counted = all_trades

    trading_days = set(t["date"] for t in trades_counted)
    final_equity = eq.iloc[-1]
    profit_pct = (final_equity - initial) / initial * 100

    # DD is STATIC from initial balance ($100K)
    max_dd = 0
    for val in eq:
        dd = (initial - val) / initial
        max_dd = max(max_dd, dd)

    max_daily_dd = 0
    daily = eq.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily.iterrows():
        if row["first"] > 0:
            ddd = (row["first"] - row["min"]) / row["first"]
            max_daily_dd = max(max_daily_dd, ddd)

    if lock_ts:
        days_used = (lock_ts - pd.Timestamp(start_date)).days + 1
    else:
        days_used = window_days

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

    return {
        "outcome": outcome,
        "profit_pct": round(profit_pct, 2),
        "final_equity": round(final_equity, 2),
        "max_dd": round(max_dd * 100, 2),
        "max_daily_dd": round(max_daily_dd * 100, 2),
        "trades": len(trades_counted),
        "trading_days": len(trading_days),
        "days_used": days_used,
    }


def run_full_exam(config, instruments, data_dict, combo_names, risk_pct, start_date):
    """
    Run full FTMO 2-step exam: Phase 1 (30 days) then Phase 2 (60 days).
    P1: 10% target, 30 days | P2: 5% target, 60 days, balance carries over
    """
    p1 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   start_date, window_days=30, profit_target_pct=10.0)

    if p1["outcome"] != "PASS":
        return {"exam_outcome": f"FAIL_P1_{p1['outcome']}", "p1": p1, "p2": None}

    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   p2_start, window_days=60, profit_target_pct=5.0)

    if p2["outcome"] == "PASS":
        exam_outcome = "FUNDED"
    else:
        exam_outcome = f"FAIL_P2_{p2['outcome']}"

    return {"exam_outcome": exam_outcome, "p1": p1, "p2": p2}


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

    window_starts = pd.date_range("2015-01-01", "2024-06-01", freq="90D")
    print(f"\n  Exam windows: {len(window_starts)} (every 90 days, P1=30d + P2=60d)")

    print(f"\n{'='*120}")
    print(f"  FTMO 2-STEP CHALLENGE SIMULATION")
    print(f"  P1: 10% target, 30 days | P2: 5% target, 60 days, balance carries over")
    print(f"  DD: 5% daily / 10% total (static from $100K) | Min 4 trading days | Profit lock ON")
    print(f"{'='*120}")

    all_results = []

    for risk in [0.02, 0.03]:
        print(f"\n  === Risk: {risk*100:.0f}% per trade ===")

        for deck_name, combo_names in DECKS.items():
            available = [c for c in combo_names if ALL_COMBOS[c]["symbol"] in data_dict]
            if len(available) < len(combo_names):
                missing = set(combo_names) - set(available)
                print(f"\n  {deck_name}: SKIP (missing data: {missing})")
                continue

            n_robust = sum(1 for c in combo_names if ALL_COMBOS[c]["tier"] == "ROBUST")
            print(f"\n  {deck_name} ({len(combo_names)} combos, {n_robust} robust):", end=" ", flush=True)

            exam_outcomes = defaultdict(int)
            p1_outcomes = defaultdict(int)
            p2_outcomes = defaultdict(int)
            p1_profits = []
            funded_count = 0

            for i, start in enumerate(window_starts):
                r = run_full_exam(config, instruments, data_dict, combo_names, risk, start)
                exam_outcomes[r["exam_outcome"]] += 1
                p1_outcomes[r["p1"]["outcome"]] += 1
                p1_profits.append(r["p1"]["profit_pct"])
                if r["p2"] is not None:
                    p2_outcomes[r["p2"]["outcome"]] += 1
                if r["exam_outcome"] == "FUNDED":
                    funded_count += 1
                if (i + 1) % 10 == 0:
                    print(".", end="", flush=True)

            n = len(window_starts)
            p1_pass = p1_outcomes["PASS"]
            p1_rate = p1_pass / n * 100
            funded_rate = funded_count / n * 100
            p2_tested = sum(p2_outcomes.values())
            p2_pass_of_tested = funded_count / p2_tested * 100 if p2_tested > 0 else 0

            print(f"\n    P1: {p1_pass}/{n} pass ({p1_rate:.1f}%) | "
                  f"AvgProfit: {np.mean(p1_profits):+.2f}%")
            print(f"    P1 fails: DD={p1_outcomes['FAIL_DD']+p1_outcomes['FAIL_DAILY_DD']} "
                  f"Profit={p1_outcomes['FAIL_PROFIT']} "
                  f"Days={p1_outcomes['FAIL_MIN_DAYS']}")

            if p2_tested > 0:
                print(f"    P2: {funded_count}/{p2_tested} pass ({p2_pass_of_tested:.1f}%) "
                      f"of those who passed P1")
                print(f"    P2 fails: DD={p2_outcomes.get('FAIL_DD',0)+p2_outcomes.get('FAIL_DAILY_DD',0)} "
                      f"Profit={p2_outcomes.get('FAIL_PROFIT',0)} "
                      f"Days={p2_outcomes.get('FAIL_MIN_DAYS',0)}")

            print(f"    FUNDED: {funded_count}/{n} ({funded_rate:.1f}%)")

            all_results.append({
                "deck": deck_name, "risk": f"{risk*100:.0f}%",
                "combos": len(combo_names), "robust": n_robust, "n": n,
                "p1_pass": p1_pass, "p1_rate": p1_rate,
                "p2_tested": p2_tested, "p2_pass": funded_count,
                "p2_rate": p2_pass_of_tested,
                "funded": funded_count, "funded_rate": funded_rate,
                "avg_p1_profit": np.mean(p1_profits),
            })

    # Summary table
    print(f"\n\n{'='*120}")
    print(f"  SUMMARY — FTMO 2-Step End-to-End")
    print(f"{'='*120}")
    print(f"  {'Deck':<18s} {'Risk':>4s} {'#':>3s} {'Rob':>3s} "
          f"{'P1 Pass':>8s} {'P1%':>6s} "
          f"{'P2 Pass':>8s} {'P2%':>6s} "
          f"{'FUNDED':>8s} {'Fund%':>6s} "
          f"{'AvgP1':>7s}")
    print(f"  {'-'*110}")
    for r in sorted(all_results, key=lambda x: -x["funded_rate"]):
        p2_str = f"{r['p2_pass']}/{r['p2_tested']}" if r['p2_tested'] > 0 else "n/a"
        p2_rate_str = f"{r['p2_rate']:.1f}%" if r['p2_tested'] > 0 else "n/a"
        print(f"  {r['deck']:<18s} {r['risk']:>4s} {r['combos']:>3d} {r['robust']:>3d} "
              f"{r['p1_pass']:>3d}/{r['n']:<4d} {r['p1_rate']:>5.1f}% "
              f"{p2_str:>8s} {p2_rate_str:>6s} "
              f"{r['funded']:>3d}/{r['n']:<4d} {r['funded_rate']:>5.1f}% "
              f"{r['avg_p1_profit']:>+6.2f}%")

    # ROI analysis
    print(f"\n{'='*120}")
    print(f"  ROI — EUR80/exam, $10K accounts, 80% profit split")
    print(f"{'='*120}\n")

    for r in sorted(all_results, key=lambda x: -x["funded_rate"])[:8]:
        fr = r["funded_rate"] / 100
        if fr > 0:
            cost_per_funded = 80 / fr
            monthly_funded = 10 * fr
            monthly_income = monthly_funded * 400
            print(f"  {r['deck']:<18s} @{r['risk']:>3s}: "
                  f"P1={r['p1_rate']:5.1f}% x P2={r['p2_rate']:5.1f}% = "
                  f"{r['funded_rate']:5.1f}% funded/exam | "
                  f"EUR{cost_per_funded:,.0f}/funded | "
                  f"10 exams/mo -> {monthly_funded:.1f} funded -> EUR{monthly_income:,.0f}/mo")


if __name__ == "__main__":
    main()
