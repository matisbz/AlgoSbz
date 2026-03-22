"""
Challenge simulation with the validated super-deck (7 combos).
Uses independent 30-day windows with fresh balance.

Usage:
    python -X utf8 scripts/superdeck_challenge.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import timedelta

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# The validated super-deck: 7 combos
SUPER_DECK = {
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
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
    },
    "MomDiv_SPY_H1": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "symbol": "SPY",
        "params": {"timeframe": "H1"},
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
    "Engulf_GBPJPY_H4": {
        "module": "algosbz.strategy.engulfing_reversal",
        "class": "EngulfingReversal",
        "symbol": "GBPJPY",
        "params": {"timeframe": "H4"},
    },
    "SwBrk_SPY_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "SPY",
        "params": {"timeframe": "H4"},
    },
}

# Test sub-decks too
DECKS = {
    "Full 7": list(SUPER_DECK.keys()),
    "Top5 PF>1.05": ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1",
                      "TPB_XTIUSD_H4", "VMR_USDJPY_H4"],
    "Top3 PF>1.1": ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "MomDiv_SPY_H1"],
    "Old deck (5 prev)": ["VMR_USDCHF_H1", "SwBrk_XTIUSD_H4", "TPB_XTIUSD_H4",
                           "VMR_USDJPY_H4"],
}

RISK_LEVELS = [0.02, 0.03]


def load_strategy(entry):
    import importlib
    mod = importlib.import_module(entry["module"])
    cls = getattr(mod, entry["class"])
    return cls(entry["params"])


def run_window(config, instruments, data_dict, combo_names, risk_pct, start_date, window_days=30):
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

    total_trades = 0
    trading_days = set()
    combined_pnl_points = []

    for combo_name in combo_names:
        entry = SUPER_DECK[combo_name]
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
            t_date = t.entry_time
            if isinstance(t_date, pd.Timestamp):
                t_date = t_date.date()
            elif hasattr(t_date, 'date'):
                t_date = t_date.date()
            if start_date.date() <= t_date < end_date.date():
                total_trades += 1
                trading_days.add(t_date)

        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                combined_pnl_points.append((ts, val - initial))

    if not combined_pnl_points:
        return {"outcome": "NO_DATA", "profit_pct": 0, "max_dd": 0, "max_daily_dd": 0,
                "trades": 0, "trading_days": 0}

    df = pd.DataFrame(combined_pnl_points, columns=["ts", "pnl"])
    combined_pnl = df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    final = combined_equity.iloc[-1]
    profit_pct = (final - initial) / initial * 100

    max_dd = 0
    for val in combined_equity:
        dd = (initial - val) / initial
        max_dd = max(max_dd, dd)

    max_daily_dd = 0
    daily = combined_equity.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily.iterrows():
        if row["first"] > 0:
            dd = (row["first"] - row["min"]) / row["first"]
            max_daily_dd = max(max_daily_dd, dd)

    if max_dd >= 0.10:
        outcome = "FAIL_DD"
    elif max_daily_dd >= 0.05:
        outcome = "FAIL_DAILY_DD"
    elif profit_pct >= 8.0 and len(trading_days) >= 4:
        outcome = "PASS"
    elif profit_pct >= 8.0:
        outcome = "FAIL_MIN_DAYS"
    else:
        outcome = "FAIL_PROFIT"

    return {
        "outcome": outcome,
        "profit_pct": round(profit_pct, 2),
        "max_dd": round(max_dd * 100, 2),
        "max_daily_dd": round(max_daily_dd * 100, 2),
        "trades": total_trades,
        "trading_days": len(trading_days),
    }


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    all_symbols = list({e["symbol"] for e in SUPER_DECK.values()})
    data_dict = {}
    print("Loading data...")
    for sym in all_symbols:
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    window_starts = pd.date_range("2015-01-01", "2024-11-01", freq="30D")
    print(f"\n  Windows: {len(window_starts)} x 30 days")

    print("\n" + "=" * 110)
    print("  SUPER-DECK CHALLENGE SIMULATION")
    print("=" * 110)

    all_rows = []
    for deck_name, combo_names in DECKS.items():
        for risk in RISK_LEVELS:
            label = f"{deck_name} @ {risk*100:.0f}%"
            print(f"\n  {label}:", end=" ", flush=True)

            outcomes = {}
            profits = []
            trades_list = []

            for i, start in enumerate(window_starts):
                r = run_window(config, instruments, data_dict, combo_names, risk, start)
                outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
                profits.append(r["profit_pct"])
                trades_list.append(r["trades"])
                if (i + 1) % 20 == 0:
                    print(".", end="", flush=True)

            n = len(window_starts)
            passes = outcomes.get("PASS", 0)
            fail_dd = outcomes.get("FAIL_DD", 0) + outcomes.get("FAIL_DAILY_DD", 0)
            fail_profit = outcomes.get("FAIL_PROFIT", 0)
            pass_rate = passes / n * 100

            print(f"\n    PASS: {passes}/{n} ({pass_rate:.1f}%) | "
                  f"FailDD: {fail_dd} | FailProfit: {fail_profit} | "
                  f"AvgProfit: {np.mean(profits):+.2f}% | AvgTrades: {np.mean(trades_list):.1f}")

            all_rows.append({
                "Deck": deck_name,
                "Risk": f"{risk*100:.0f}%",
                "PASS": passes,
                "Pass%": f"{pass_rate:.1f}",
                "FailDD": fail_dd,
                "FailProfit": fail_profit,
                "AvgProfit": f"{np.mean(profits):+.2f}",
                "AvgTrades": f"{np.mean(trades_list):.1f}",
                "Combos": len(combo_names),
            })

    print("\n\n" + "=" * 110)
    print("  SUMMARY")
    print("=" * 110)
    df = pd.DataFrame(all_rows)
    print(df.to_string(index=False))

    print("\n  ROI Analysis ($500/exam, 60% P2 pass rate, $100K account):")
    for r in all_rows:
        p1 = float(r["Pass%"]) / 100
        if p1 > 0:
            funded = p1 * 0.60
            cost = 500 / funded if funded > 0 else float("inf")
            annual = 12 * funded
            print(f"    {r['Deck']:20s} @{r['Risk']:>3s}: "
                  f"P1={p1*100:5.1f}% -> {funded*100:5.1f}% funded/exam "
                  f"(${cost:,.0f}/funded, ~{annual:.1f}/yr)")


if __name__ == "__main__":
    main()
