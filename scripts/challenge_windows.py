"""
Proper FTMO challenge simulation using independent 30-day windows.

Each window:
1. Starts with fresh $100K balance
2. Runs the portfolio strategies for 30 calendar days
3. Applies FTMO DD limits (5% daily, 10% total) WITHIN the window only
4. Checks if profit target (8%) is reached
5. Records PASS/FAIL with reason

This correctly simulates buying one FTMO exam per month.

Usage:
    python -X utf8 scripts/challenge_windows.py
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
from algosbz.data.resampler import resample
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)  # Suppress warnings for bulk runs

ALL_COMBOS = {
    "VMR_USDCHF": {"strategy": "vol_mean_reversion", "symbol": "USDCHF",
                    "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2,
                               "sl_atr_mult": 3.0, "tp_atr_mult": 4.0,
                               "session_start": 0, "session_end": 23}},
    "TPB_XTIUSD": {"strategy": "trend_pullback", "symbol": "XTIUSD",
                    "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                               "sl_atr_mult": 2.0, "tp_atr_mult": 2.5,
                               "session_start": 0, "session_end": 23}},
    "H4MR_XTIUSD": {"strategy": "h4_mean_reversion", "symbol": "XTIUSD",
                     "params": {"bb_std": 2.0, "rsi_oversold": 30, "rsi_overbought": 70,
                                "adx_max": 30, "sl_atr_mult": 1.5, "tp_atr_mult": 2.0}},
    "SwBrk_XTIUSD": {"strategy": "swing_breakout", "symbol": "XTIUSD",
                      "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 15,
                                 "sl_atr_mult": 1.0, "tp_atr_mult": 2.0}},
    "SwBrk_USDJPY": {"strategy": "swing_breakout", "symbol": "USDJPY",
                      "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 20,
                                 "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}},
}

STRATEGY_MAP = {
    "vol_mean_reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "trend_pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
    "h4_mean_reversion": ("algosbz.strategy.h4_mean_reversion", "H4MeanReversion"),
    "swing_breakout": ("algosbz.strategy.swing_breakout", "SwingBreakout"),
}

PORTFOLIOS = {
    "All5": list(ALL_COMBOS.keys()),
    "Top3_PF": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD"],
    "Top4_PF": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD", "SwBrk_USDJPY"],
}

RISK_LEVELS = [0.02, 0.03, 0.04, 0.05]


def load_strategy_class(name: str):
    import importlib
    module_path, class_name = STRATEGY_MAP[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def run_single_window(config, instruments, data_dict, combo_names, risk_pct,
                      start_date, window_days=30):
    """
    Run a single 30-day challenge window with fresh balance.
    Returns dict with outcome, profit, DD, trades, etc.
    """
    end_date = start_date + timedelta(days=window_days)
    initial = config.account.initial_balance

    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    # Use FTMO actual limits within each window
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    # No anti-martingale — pure risk
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )

    total_trades = 0
    all_equity_points = []
    trading_days = set()

    for combo_name in combo_names:
        entry = ALL_COMBOS[combo_name]
        sym = entry["symbol"]
        if sym not in data_dict:
            continue

        full_data = data_dict[sym]
        # Slice to window with some lookback for indicators
        lookback = timedelta(days=120)  # Need history for indicators
        slice_start = start_date - lookback
        mask = (full_data.index >= pd.Timestamp(slice_start)) & (full_data.index < pd.Timestamp(end_date))
        window_data = full_data[mask]

        if window_data.empty:
            continue

        cls = load_strategy_class(entry["strategy"])
        strategy = cls(entry.get("params", {}))

        engine = BacktestEngine(cfg, instruments.get(sym, instruments.get(sym)),
                                EquityManager(eq_cfg))
        try:
            result = engine.run(strategy, window_data, sym)
        except Exception:
            continue

        # Only count trades that happened WITHIN the window
        for t in result.trades:
            t_date = t.entry_time.date() if hasattr(t.entry_time, 'date') else t.entry_time
            if isinstance(t_date, pd.Timestamp):
                t_date = t_date.date()
            if start_date.date() <= t_date < end_date.date():
                total_trades += 1
                trading_days.add(t_date)

        # Collect equity within window
        for ts, val in result.equity_curve.items():
            if pd.Timestamp(start_date) <= ts < pd.Timestamp(end_date):
                all_equity_points.append((ts, val))

    if not all_equity_points:
        return {
            "outcome": "NO_DATA",
            "profit_pct": 0,
            "max_daily_dd": 0,
            "max_total_dd": 0,
            "trades": 0,
            "trading_days": 0,
        }

    # Merge equity from all combos
    # Since each combo starts at initial_balance independently,
    # combined equity = initial + sum of (combo_equity - initial) for each combo
    # Group by timestamp and compute combined equity
    eq_df = pd.DataFrame(all_equity_points, columns=["ts", "equity"])
    # Each combo independently: combined PnL = sum of individual PnLs
    # Individual PnL = equity - initial for each combo
    eq_df["pnl"] = eq_df["equity"] - initial
    combined_pnl = eq_df.groupby("ts")["pnl"].sum()
    combined_equity = initial + combined_pnl
    combined_equity = combined_equity.sort_index()

    if combined_equity.empty:
        return {
            "outcome": "NO_DATA",
            "profit_pct": 0,
            "max_daily_dd": 0,
            "max_total_dd": 0,
            "trades": 0,
            "trading_days": 0,
        }

    # Calculate metrics
    final_equity = combined_equity.iloc[-1]
    profit_pct = (final_equity - initial) / initial * 100

    # Max overall DD from initial (static, like FTMO)
    max_total_dd = 0
    for val in combined_equity:
        dd = (initial - val) / initial
        max_total_dd = max(max_total_dd, dd)

    # Max daily DD
    max_daily_dd = 0
    daily_eq = combined_equity.resample("1D").agg(["first", "min"]).dropna()
    for _, row in daily_eq.iterrows():
        if row["first"] > 0:
            dd = (row["first"] - row["min"]) / row["first"]
            max_daily_dd = max(max_daily_dd, dd)

    # Determine outcome
    if max_total_dd >= 0.10:
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
        "max_daily_dd": round(max_daily_dd * 100, 2),
        "max_total_dd": round(max_total_dd * 100, 2),
        "trades": total_trades,
        "trading_days": len(trading_days),
    }


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    symbols = list({v["symbol"] for v in ALL_COMBOS.values()})
    data_dict = {}
    print("Loading data...")
    for sym in symbols:
        data_dict[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    # Generate window start dates (every 30 days from 2015-01-01)
    window_starts = pd.date_range("2015-01-01", "2024-11-01", freq="30D")
    print(f"\n  Total windows: {len(window_starts)} (30-day each, starting every 30 days)")

    print("\n" + "=" * 120)
    print("  FTMO CHALLENGE SIMULATION — Independent 30-day windows")
    print("  Each window: fresh $100K, FTMO rules (8% target, 5% daily DD, 10% total DD)")
    print("=" * 120)

    all_results = []

    for port_name, combo_names in PORTFOLIOS.items():
        for risk in RISK_LEVELS:
            label = f"{port_name} @ {risk*100:.0f}%"
            print(f"\n  {label}:", end=" ", flush=True)

            outcomes = {"PASS": 0, "FAIL_DD": 0, "FAIL_DAILY_DD": 0,
                       "FAIL_PROFIT": 0, "FAIL_MIN_DAYS": 0, "NO_DATA": 0}
            profits = []
            trades_list = []
            dd_list = []
            pass_months = []

            for i, start in enumerate(window_starts):
                r = run_single_window(config, instruments, data_dict, combo_names,
                                     risk, start, window_days=30)
                outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
                profits.append(r["profit_pct"])
                trades_list.append(r["trades"])
                dd_list.append(r["max_total_dd"])

                if r["outcome"] == "PASS":
                    pass_months.append(start.strftime("%Y-%m"))

                # Progress dots
                if (i + 1) % 20 == 0:
                    print(".", end="", flush=True)

            total_windows = len(window_starts)
            pass_rate = outcomes["PASS"] / total_windows * 100
            fail_dd_rate = (outcomes["FAIL_DD"] + outcomes["FAIL_DAILY_DD"]) / total_windows * 100
            avg_profit = np.mean(profits)
            avg_trades = np.mean(trades_list)
            avg_dd = np.mean(dd_list)

            print(f"\n    PASS: {outcomes['PASS']}/{total_windows} ({pass_rate:.1f}%) | "
                  f"Fail DD: {outcomes['FAIL_DD']+outcomes['FAIL_DAILY_DD']} | "
                  f"Fail Profit: {outcomes['FAIL_PROFIT']} | "
                  f"Avg profit: {avg_profit:+.2f}% | Avg trades: {avg_trades:.1f} | "
                  f"Avg DD: {avg_dd:.1f}%")

            if pass_months:
                print(f"    PASS months: {', '.join(pass_months[:10])}"
                      f"{'...' if len(pass_months) > 10 else ''}")

            all_results.append({
                "Portfolio": port_name,
                "Risk": f"{risk*100:.0f}%",
                "Windows": total_windows,
                "PASS": outcomes["PASS"],
                "Pass%": f"{pass_rate:.1f}",
                "FailDD": outcomes["FAIL_DD"] + outcomes["FAIL_DAILY_DD"],
                "FailProfit": outcomes["FAIL_PROFIT"],
                "AvgProfit%": f"{avg_profit:+.2f}",
                "AvgTrades": f"{avg_trades:.1f}",
                "AvgDD%": f"{avg_dd:.1f}",
            })

    # Summary table
    print("\n\n" + "=" * 120)
    print("  SUMMARY: FTMO Phase 1 Pass Rate by Configuration")
    print("=" * 120)
    df = pd.DataFrame(all_results)
    print(df.to_string(index=False))

    # ROI Analysis
    print("\n" + "=" * 120)
    print("  ROI ANALYSIS: Is this a viable exam factory?")
    print("=" * 120)
    print("  Assumptions: $500/exam, FTMO $100K account, 80% profit split")
    print("  Phase 2 pass rate estimated at 60% (5% in 60 days is easier)")
    print()
    for r in all_results:
        p1_rate = float(r["Pass%"]) / 100
        if p1_rate > 0:
            p2_rate = 0.60
            funded_rate = p1_rate * p2_rate
            exams_per_funded = 1 / funded_rate if funded_rate > 0 else float("inf")
            cost_per_funded = exams_per_funded * 500
            # Funded account: assume averages 5% in first month, then consistent 3%/month
            # Simplified: first payout ~= 5% x $100K x 80% = $4K
            annual_funded = 12 * funded_rate  # Funded accounts per year
            print(f"  {r['Portfolio']:10s} @ {r['Risk']:3s}: "
                  f"P1={p1_rate*100:5.1f}% x P2=60% = {funded_rate*100:5.1f}% funded/exam | "
                  f"{exams_per_funded:5.1f} exams/funded (${cost_per_funded:,.0f}) | "
                  f"~{annual_funded:.1f} funded/year")


if __name__ == "__main__":
    main()
