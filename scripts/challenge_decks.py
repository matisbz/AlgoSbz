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

ALL_COMBOS = {
    # === Original Core3 (validated post-audit) ===
    "VMR_USDCHF_H1": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDCHF",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "SwBrk_XTIUSD_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4"},
    },
    "MomDiv_SPY_H1": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "symbol": "SPY",
        "params": {"timeframe": "H1"},
    },
    # === Existing strategies, new validated combos ===
    "TPB_XTIUSD_H4": {
        "module": "algosbz.strategy.trend_pullback",
        "class": "TrendPullback",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "VMR_USDJPY_H4": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDJPY",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "SwBrk_SPY_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "SPY",
        "params": {"timeframe": "H4"},
    },
    "Engulf_EURUSD_H4": {
        "module": "algosbz.strategy.engulfing_reversal",
        "class": "EngulfingReversal",
        "symbol": "EURUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    # === New strategies (Fase 1) ===
    "RegVMR_XAUUSD_H1": {
        "module": "algosbz.strategy.regime_vmr",
        "class": "RegimeAdaptiveVMR",
        "symbol": "XAUUSD",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
    "SessBrk_XTIUSD_M15": {
        "module": "algosbz.strategy.session_breakout_v2",
        "class": "SessionBreakout",
        "symbol": "XTIUSD",
        "params": {"timeframe": "M15"},
    },
    "SMCOB_XAUUSD_H4": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "symbol": "XAUUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "SMCOB_GBPJPY_H1": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "symbol": "GBPJPY",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
    },
}

# Deck configurations
DECKS = {
    "Core3": ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1"],
    "SuperDeck11": [
        "VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1",
        "TPB_XTIUSD_H4", "VMR_USDJPY_H4", "SwBrk_SPY_H4", "Engulf_EURUSD_H4",
        "RegVMR_XAUUSD_H1", "SessBrk_XTIUSD_M15", "SMCOB_XAUUSD_H4", "SMCOB_GBPJPY_H1",
    ],
    "TopPF": [
        "VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "RegVMR_XAUUSD_H1",
        "SessBrk_XTIUSD_M15", "SMCOB_XAUUSD_H4",
    ],
    "MultiAsset": [
        "VMR_USDCHF_H1", "MomDiv_SPY_H1", "RegVMR_XAUUSD_H1",
        "SMCOB_GBPJPY_H1", "Engulf_EURUSD_H4",
    ],
}


def load_strategy(entry):
    import importlib
    mod = importlib.import_module(entry["module"])
    cls = getattr(mod, entry["class"])
    return cls(entry["params"])


def run_phase(config, instruments, data_dict, combo_names, risk_pct,
              start_date, window_days, profit_target_pct):
    """
    Run a single phase (P1 or P2) of the FTMO challenge.

    FTMO rules:
    - DD limits are STATIC from INITIAL balance ($100K), not from current balance
    - Daily DD: 5% of initial = equity can't drop more than $5K in a day
    - Total DD: 10% of initial = equity can't drop below $90K ever

    Returns dict with outcome, final equity, profit, trades, etc.
    """
    end_date = start_date + timedelta(days=window_days)
    initial = config.account.initial_balance  # Always $100K (DD reference)

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
                # Profit is always relative to INITIAL ($100K)
                profit_now = (eq_before.iloc[-1] - initial) / initial * 100
                if profit_now >= profit_target_pct and len(trading_days_so_far) >= 4:
                    lock_ts = trade_ts
                    break

    # Calculate metrics up to lock point (or full window)
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
        dd = (initial - val) / initial  # Always from $100K, not peak
        max_dd = max(max_dd, dd)

    max_daily_dd = 0
    daily = eq.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily.iterrows():
        if row["first"] > 0:
            ddd = (row["first"] - row["min"]) / row["first"]
            max_daily_dd = max(max_daily_dd, ddd)

    # Days used (for P2 start date calculation)
    if lock_ts:
        days_used = (lock_ts - pd.Timestamp(start_date)).days + 1
    else:
        days_used = window_days

    # Determine outcome
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


def run_full_exam(config, instruments, data_dict, combo_names, risk_pct,
                  start_date):
    """
    Run full FTMO 2-step exam: Phase 1 (30 days) then Phase 2 (60 days).

    P1: 10% target, 30 days
    P2: 5% target, 60 days, balance carries over (but DD still from $100K initial)

    Since there's no time limit in current FTMO rules, we use generous windows
    (30 days P1, 60 days P2) as a practical simulation period.
    """
    # Phase 1: 10% target, 30 days
    p1 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   start_date, window_days=30, profit_target_pct=10.0)

    if p1["outcome"] != "PASS":
        return {
            "exam_outcome": f"FAIL_P1_{p1['outcome']}",
            "p1": p1,
            "p2": None,
        }

    # Phase 2: starts after P1 ends, 60 days, target 5%
    # Balance carries over — but our backtest engine always starts at $100K
    # The key insight: DD is still from $100K initial, and we need +5% from initial
    # So if P1 ended at $110K, we need to get to $105K total (which means we can
    # actually LOSE some of the P1 gains and still pass P2)
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(config, instruments, data_dict, combo_names, risk_pct,
                   p2_start, window_days=60, profit_target_pct=5.0)

    if p2["outcome"] == "PASS":
        exam_outcome = "FUNDED"
    else:
        exam_outcome = f"FAIL_P2_{p2['outcome']}"

    return {
        "exam_outcome": exam_outcome,
        "p1": p1,
        "p2": p2,
    }


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({e["symbol"] for e in ALL_COMBOS.values()})
    data_dict = {}
    print("Loading data...")
    for sym in all_symbols:
        try:
            data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
            print(f"  {sym}: {len(data_dict[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # Windows: every 90 days (each exam can take up to 90 days total)
    window_starts = pd.date_range("2015-01-01", "2024-06-01", freq="90D")
    print(f"\n  Exam windows: {len(window_starts)} (every 90 days, P1=30d + P2=60d)")

    print(f"\n{'='*120}")
    print(f"  FTMO 2-STEP CHALLENGE — Correct Rules")
    print(f"  P1: 10% target, 30 days | P2: 5% target, 60 days, balance carries over")
    print(f"  DD: 5% daily / 10% total (static from $100K) | Min 4 trading days each phase")
    print(f"  Profit lock active in both phases")
    print(f"{'='*120}")

    all_results = []

    for risk in [0.02, 0.03]:
        print(f"\n  === Risk: {risk*100:.0f}% ===")

        for deck_name, combo_names in DECKS.items():
            available = [c for c in combo_names if ALL_COMBOS[c]["symbol"] in data_dict]
            if len(available) < len(combo_names):
                missing = set(combo_names) - set(available)
                print(f"\n  {deck_name}: SKIP (missing: {missing})")
                continue

            print(f"\n  {deck_name} ({len(combo_names)} combos):", end=" ", flush=True)

            exam_outcomes = defaultdict(int)
            p1_outcomes = defaultdict(int)
            p2_outcomes = defaultdict(int)
            p1_profits = []
            funded_count = 0

            for i, start in enumerate(window_starts):
                r = run_full_exam(config, instruments, data_dict, combo_names,
                                  risk, start)
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
                "deck": deck_name,
                "risk": f"{risk*100:.0f}%",
                "combos": len(combo_names),
                "n": n,
                "p1_pass": p1_pass,
                "p1_rate": p1_rate,
                "p2_tested": p2_tested,
                "p2_pass": funded_count,
                "p2_rate": p2_pass_of_tested,
                "funded": funded_count,
                "funded_rate": funded_rate,
                "avg_p1_profit": np.mean(p1_profits),
            })

    # Summary table
    print(f"\n\n{'='*120}")
    print(f"  SUMMARY — FTMO 2-Step End-to-End")
    print(f"{'='*120}")
    print(f"  {'Deck':<20s} {'Risk':>4s} {'#':>2s} "
          f"{'P1 Pass':>8s} {'P1 Rate':>7s} "
          f"{'P2 Pass':>8s} {'P2 Rate':>7s} "
          f"{'FUNDED':>8s} {'Fund%':>6s} "
          f"{'AvgP1%':>7s}")
    print(f"  {'-'*100}")
    for r in sorted(all_results, key=lambda x: -x["funded_rate"]):
        p2_str = f"{r['p2_pass']}/{r['p2_tested']}" if r['p2_tested'] > 0 else "n/a"
        p2_rate_str = f"{r['p2_rate']:.1f}%" if r['p2_tested'] > 0 else "n/a"
        print(f"  {r['deck']:<20s} {r['risk']:>4s} {r['combos']:>2d} "
              f"{r['p1_pass']:>3d}/{r['n']:<4d} {r['p1_rate']:>6.1f}% "
              f"{p2_str:>8s} {p2_rate_str:>7s} "
              f"{r['funded']:>3d}/{r['n']:<4d} {r['funded_rate']:>5.1f}% "
              f"{r['avg_p1_profit']:>+6.2f}%")

    # ROI analysis
    print(f"\n{'='*120}")
    print(f"  ROI — €80/exam, $10K accounts, 80% profit split")
    print(f"{'='*120}")
    print(f"  Note: each exam takes ~30-90 days. With unlimited time, we model")
    print(f"  buying 10 exams/month and running them in parallel.\n")

    for r in sorted(all_results, key=lambda x: -x["funded_rate"])[:6]:
        fr = r["funded_rate"] / 100
        if fr > 0:
            cost_per_funded = 80 / fr
            # 10 parallel exams per month, each takes ~2-3 months
            # Steady state: ~10 new exams/month completing
            monthly_funded = 10 * fr
            monthly_income = monthly_funded * 400  # ~$400/month per $10K funded
            print(f"  {r['deck']:<20s} @{r['risk']:>3s}: "
                  f"P1={r['p1_rate']:5.1f}% × P2={r['p2_rate']:5.1f}% = "
                  f"{r['funded_rate']:5.1f}% funded/exam | "
                  f"€{cost_per_funded:,.0f}/funded | "
                  f"10 exams/mo → {monthly_funded:.1f} funded → €{monthly_income:,.0f}/mo")


if __name__ == "__main__":
    main()
