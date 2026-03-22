"""
Analyze DD fails: what causes them and can we prevent them?

For each 30-day window, track:
- When the DD fail happens (day 1-30)
- Profit at time of DD fail (were we ahead before blowing?)
- Number of trades before DD
- How many consecutive losses before DD

This tells us if DD is from:
A) Bad luck early (first few trades lose) → need ramp-up protection
B) Overextension after winning (gave back gains) → need profit lock
C) Correlation: multiple combos lose simultaneously → need exposure limits

Usage:
    python -X utf8 scripts/dd_analysis.py
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

DECK = {
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
}


def load_strategy(entry):
    import importlib
    mod = importlib.import_module(entry["module"])
    cls = getattr(mod, entry["class"])
    return cls(entry["params"])


def run_detailed_window(config, instruments, data_dict, risk_pct, start_date, window_days=30):
    """Run window tracking detailed trade-by-trade PnL."""
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

    all_trades = []  # (timestamp, pnl, combo_name)
    combined_pnl_points = []

    for combo_name, entry in DECK.items():
        sym = entry["symbol"]
        if sym not in data_dict:
            continue

        full_data = data_dict[sym]
        lookback = timedelta(days=120)
        slice_start = start_date - lookback
        mask = (full_data.index >= pd.Timestamp(slice_start)) & (full_data.index < pd.Timestamp(end_date))
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
                all_trades.append({
                    "ts": t_ts,
                    "date": t_date,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl / initial * 100,
                    "combo": combo_name,
                })

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                combined_pnl_points.append((ts, val - initial))

    if not combined_pnl_points:
        return None

    # Combined equity
    df = pd.DataFrame(combined_pnl_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    # Track equity progression
    equity_by_day = combined_equity.resample("1D").last().dropna()

    # Find peak profit and DD timeline
    peak_profit_pct = 0
    dd_day = None
    dd_profit_at_fail = None
    max_dd = 0

    for i, (ts, val) in enumerate(combined_equity.items()):
        profit_pct = (val - initial) / initial * 100
        peak_profit_pct = max(peak_profit_pct, profit_pct)
        dd = (initial - val) / initial
        if dd > max_dd:
            max_dd = dd
            if dd >= 0.10:
                dd_day = (ts.date() - start_date.date()).days
                dd_profit_at_fail = profit_pct
                break

    # Daily DD
    max_daily_dd = 0
    daily_eq = combined_equity.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily_eq.iterrows():
        if row["first"] > 0:
            ddd = (row["first"] - row["min"]) / row["first"]
            if ddd > max_daily_dd:
                max_daily_dd = ddd
                if ddd >= 0.05:
                    break

    final = combined_equity.iloc[-1]
    profit_pct = (final - initial) / initial * 100

    # Classify
    trading_days = set()
    for t in all_trades:
        trading_days.add(t["date"])

    if max_dd >= 0.10:
        outcome = "FAIL_DD"
    elif max_daily_dd >= 0.05:
        outcome = "FAIL_DAILY_DD"
    elif profit_pct >= 8.0 and len(trading_days) >= 4:
        outcome = "PASS"
    else:
        outcome = "FAIL_PROFIT"

    # Sort trades chronologically
    all_trades.sort(key=lambda x: x["ts"])

    # Find consecutive losses before DD
    consec_losses = 0
    max_consec_losses = 0
    for t in all_trades:
        if t["pnl"] < 0:
            consec_losses += 1
            max_consec_losses = max(max_consec_losses, consec_losses)
        else:
            consec_losses = 0

    # Check if same-day losses from multiple combos
    same_day_losses = defaultdict(list)
    for t in all_trades:
        if t["pnl"] < 0:
            same_day_losses[t["date"]].append(t)

    correlated_loss_days = sum(1 for d, trades in same_day_losses.items() if len(trades) >= 2)

    return {
        "start": start_date.date(),
        "outcome": outcome,
        "profit_pct": round(profit_pct, 2),
        "peak_profit_pct": round(peak_profit_pct, 2),
        "max_dd_pct": round(max_dd * 100, 2),
        "max_daily_dd_pct": round(max_daily_dd * 100, 2),
        "trades": len(all_trades),
        "trading_days": len(trading_days),
        "dd_day": dd_day,
        "dd_profit_at_fail": round(dd_profit_at_fail, 2) if dd_profit_at_fail else None,
        "max_consec_losses": max_consec_losses,
        "correlated_loss_days": correlated_loss_days,
        "trade_log": all_trades,
    }


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    symbols = list({e["symbol"] for e in DECK.values()})
    data_dict = {}
    print("Loading data...")
    for sym in symbols:
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    window_starts = pd.date_range("2015-01-01", "2024-11-01", freq="30D")

    print(f"\nRunning {len(window_starts)} windows with detailed tracking...")

    dd_fails = []
    daily_dd_fails = []
    passes = []
    profit_fails = []

    for start in window_starts:
        r = run_detailed_window(config, instruments, data_dict, 0.02, start)
        if r is None:
            continue
        if r["outcome"] == "FAIL_DD":
            dd_fails.append(r)
        elif r["outcome"] == "FAIL_DAILY_DD":
            daily_dd_fails.append(r)
        elif r["outcome"] == "PASS":
            passes.append(r)
        else:
            profit_fails.append(r)

    total = len(dd_fails) + len(daily_dd_fails) + len(passes) + len(profit_fails)
    print(f"\n  Results: {len(passes)} PASS, {len(dd_fails)} FAIL_DD, "
          f"{len(daily_dd_fails)} FAIL_DAILY_DD, {len(profit_fails)} FAIL_PROFIT")

    # ── Analyze DD fails ──
    print(f"\n{'='*100}")
    print(f"  DD FAIL ANALYSIS ({len(dd_fails)} total DD + {len(daily_dd_fails)} daily DD)")
    print(f"{'='*100}")

    all_dd = dd_fails + daily_dd_fails

    if all_dd:
        # When do DD fails happen?
        dd_days = [r["dd_day"] for r in dd_fails if r["dd_day"] is not None]
        if dd_days:
            print(f"\n  When DD fails happen (day within 30-day window):")
            print(f"    Avg day: {np.mean(dd_days):.1f}")
            print(f"    Median day: {np.median(dd_days):.0f}")
            early = sum(1 for d in dd_days if d <= 10)
            mid = sum(1 for d in dd_days if 10 < d <= 20)
            late = sum(1 for d in dd_days if d > 20)
            print(f"    Day 1-10: {early} ({early/len(dd_days)*100:.0f}%)")
            print(f"    Day 11-20: {mid} ({mid/len(dd_days)*100:.0f}%)")
            print(f"    Day 21-30: {late} ({late/len(dd_days)*100:.0f}%)")

        # Were we profitable before DD?
        profitable_before_dd = sum(1 for r in all_dd if r["peak_profit_pct"] > 2)
        print(f"\n  Were we ahead before DD fail?")
        print(f"    Peak profit > 2% before failing: {profitable_before_dd}/{len(all_dd)}")
        for r in all_dd:
            if r["peak_profit_pct"] > 2:
                print(f"      {r['start']}: peaked at +{r['peak_profit_pct']}%, "
                      f"then failed with {r['profit_pct']:+.1f}%")

        # Consecutive losses
        consec = [r["max_consec_losses"] for r in all_dd]
        print(f"\n  Consecutive losses in DD fail windows:")
        print(f"    Avg max consecutive losses: {np.mean(consec):.1f}")
        print(f"    Max consecutive losses: {max(consec)}")

        # Correlated losses
        corr = [r["correlated_loss_days"] for r in all_dd]
        print(f"\n  Correlated losses (multiple combos lose same day):")
        print(f"    Avg correlated loss days: {np.mean(corr):.1f}")
        print(f"    Windows with 2+ correlated days: {sum(1 for c in corr if c >= 2)}/{len(all_dd)}")

        # Trades before DD
        trades_before = [r["trades"] for r in all_dd]
        print(f"\n  Trades in DD fail windows:")
        print(f"    Avg trades: {np.mean(trades_before):.1f}")
        print(f"    Min trades: {min(trades_before)}")

    # ── Analyze PASS windows ──
    print(f"\n{'='*100}")
    print(f"  PASS ANALYSIS ({len(passes)} windows)")
    print(f"{'='*100}")
    if passes:
        pass_profits = [r["profit_pct"] for r in passes]
        pass_trades = [r["trades"] for r in passes]
        pass_dd = [r["max_dd_pct"] for r in passes]
        print(f"  Avg profit: {np.mean(pass_profits):+.2f}%")
        print(f"  Avg trades: {np.mean(pass_trades):.1f}")
        print(f"  Avg max DD: {np.mean(pass_dd):.1f}%")
        print(f"  Max DD in a PASS window: {max(pass_dd):.1f}%")

    # ── Analyze FAIL_PROFIT that were close ──
    print(f"\n{'='*100}")
    print(f"  NEAR-MISS ANALYSIS (FAIL_PROFIT windows that got close)")
    print(f"{'='*100}")
    near_misses = [r for r in profit_fails if r["peak_profit_pct"] >= 5.0]
    print(f"  Windows that peaked >= 5% but didn't pass: {len(near_misses)}/{len(profit_fails)}")
    for r in sorted(near_misses, key=lambda x: x["peak_profit_pct"], reverse=True):
        print(f"    {r['start']}: peaked {r['peak_profit_pct']:+.1f}%, "
              f"ended {r['profit_pct']:+.1f}%, {r['trades']} trades")

    # ── Actionable insights ──
    print(f"\n{'='*100}")
    print(f"  ACTIONABLE INSIGHTS")
    print(f"{'='*100}")

    if all_dd:
        early_dd = sum(1 for r in dd_fails if r.get("dd_day") and r["dd_day"] <= 7)
        total_dd = len(dd_fails)
        print(f"\n  1. EARLY DD PROTECTION: {early_dd}/{total_dd} DD fails happen in first week")
        print(f"     -> Reduce risk in first 3-5 trades of each window")

        gave_back = sum(1 for r in all_dd if r["peak_profit_pct"] > 3)
        print(f"\n  2. PROFIT LOCK: {gave_back}/{len(all_dd)} DD fails had peak > 3%")
        print(f"     -> When ahead by 3%+, reduce risk to protect gains")

        high_corr = sum(1 for r in all_dd if r["correlated_loss_days"] >= 2)
        print(f"\n  3. CORRELATION: {high_corr}/{len(all_dd)} DD fails had correlated loss days")
        print(f"     -> Limit concurrent positions on same instrument")


if __name__ == "__main__":
    main()
