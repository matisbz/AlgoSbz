"""
Simulate Decorr16_A deck on a personal account (no FTMO rules).
No profit targets, no phase transitions, no balance resets.
Just continuous compounding with portfolio controls + anti-martingale.

Usage:
    python -X utf8 scripts/personal_account_sim.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
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
import importlib

DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
    "StrBrk_GBPJPY_slow_H4", "EMArib_XNGUSD_loose_H4", "SMCOB_XAUUSD_H4",
    "SwBrk_SPY_fast_H4",
]

# Portfolio controls (same as exam mode)
DAILY_CAP_PCT = 2.5
COOLDOWN = 1          # max losses per combo per day
MAX_INSTR_PER_DAY = 2
MAX_DAILY_LOSSES = 3

# Anti-martingale DD tiers
# For FTMO: aggressive (stop at 8% to protect 10% limit)
DD_TIERS_FTMO = [
    (0.03, 1.0), (0.05, 0.5), (0.07, 0.25), (0.08, 0.0),
]
# For personal account: more relaxed (no hard DD limit to protect)
DD_TIERS_PERSONAL = [
    (0.05, 1.0),   # 0-5% DD: full risk
    (0.10, 0.5),   # 5-10% DD: half risk
    (0.15, 0.25),  # 10-15% DD: quarter risk
    (1.00, 0.10),  # >15%: minimum risk (never fully stop)
]


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict):
    """Pre-compute all trades at 2% risk with loose DD (raw trades)."""
    print(f"\n  Pre-computing trades at 2% risk...")
    streams = {}
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.50
    cfg.risk.max_dd_limit = 0.50
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.50, 1.0)], daily_stop_threshold=0.50,
        progressive_trades=0, consecutive_win_bonus=0,
    )
    for combo_name in DECK:
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
                    "ts": ts, "date": ts.date(), "pnl": t.pnl, "combo": combo_name,
                    "direction": "BUY" if t.pnl_pips is None or t.entry_price < (t.exit_price or 0) else "SELL",
                })
        streams[combo_name] = trades
        if trades:
            pnl_total = sum(t["pnl"] for t in trades)
            print(f"    {combo_name}: {len(trades)} trades, PnL=${pnl_total:+,.0f}")
    return streams


def simulate_personal_account(streams, initial=10000, risk_pct=0.02):
    """
    Simulate continuous trading on a personal account.
    No FTMO rules. Anti-martingale + portfolio controls.
    Trades are scaled relative to current equity (compounding).
    """
    # Merge all trades into single timeline
    all_trades = []
    for combo in DECK:
        if combo not in streams:
            continue
        for t in streams[combo]:
            all_trades.append(t)
    all_trades.sort(key=lambda x: x["ts"])

    if not all_trades:
        return None

    equity = initial
    high_water = initial
    current_day = None
    daily_start_eq = initial
    combo_day_losses = defaultdict(int)
    instr_day_trades = defaultdict(int)
    total_daily_losses = 0
    daily_stopped = False

    # Tracking
    equity_curve = []  # (date, equity)
    monthly_pnl = {}   # "YYYY-MM" -> pnl
    yearly_stats = {}   # year -> {trades, wins, pnl, max_dd, ...}
    max_dd = 0
    max_dd_pct = 0
    total_trades = 0
    total_wins = 0

    for t in all_trades:
        # New day reset
        if t["date"] != current_day:
            if current_day is not None:
                equity_curve.append((current_day, equity))
            current_day = t["date"]
            daily_start_eq = equity
            combo_day_losses.clear()
            instr_day_trades.clear()
            total_daily_losses = 0
            daily_stopped = False

        # Portfolio controls
        if daily_stopped:
            continue
        combo = t["combo"]
        if combo_day_losses[combo] >= COOLDOWN:
            continue
        instrument = ALL_COMBOS[combo]["symbol"]
        if instr_day_trades[instrument] >= MAX_INSTR_PER_DAY:
            continue
        if total_daily_losses >= MAX_DAILY_LOSSES:
            continue

        # Anti-martingale multiplier
        dd_from_hw = (high_water - equity) / high_water if high_water > 0 else 0
        mult = 0.0
        for threshold, m in DD_TIERS_PERSONAL:
            if dd_from_hw < threshold:
                mult = m
                break

        if mult <= 0:
            continue

        # Scale PnL: trades were computed at 2% on $100K.
        # Scale to current equity with multiplier.
        # pnl_scaled = raw_pnl * (equity / 100000) * mult
        pnl = t["pnl"] * (equity / 100000) * mult
        equity += pnl
        total_trades += 1

        instr_day_trades[instrument] += 1
        if pnl > 0:
            total_wins += 1
        if pnl < 0:
            combo_day_losses[combo] += 1
            total_daily_losses += 1

        # Daily cap
        daily_loss_pct = (daily_start_eq - equity) / daily_start_eq * 100
        if daily_loss_pct >= DAILY_CAP_PCT:
            daily_stopped = True

        # Update high water mark
        if equity > high_water:
            high_water = equity

        # Max drawdown
        dd = (high_water - equity) / high_water
        if dd > max_dd_pct:
            max_dd_pct = dd

        # Yearly tracking
        year = t["date"].year
        if year not in yearly_stats:
            yearly_stats[year] = {
                "trades": 0, "wins": 0, "start_eq": equity - pnl,
                "pnl": 0, "max_dd": 0, "hw": equity - pnl,
            }
        ys = yearly_stats[year]
        ys["trades"] += 1
        if pnl > 0:
            ys["wins"] += 1
        ys["pnl"] += pnl
        if equity > ys["hw"]:
            ys["hw"] = equity
        yr_dd = (ys["hw"] - equity) / ys["hw"] if ys["hw"] > 0 else 0
        if yr_dd > ys["max_dd"]:
            ys["max_dd"] = yr_dd

        # Monthly tracking
        month_key = t["date"].strftime("%Y-%m")
        monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + pnl

    # Final
    if current_day:
        equity_curve.append((current_day, equity))

    return {
        "initial": initial,
        "final_equity": equity,
        "total_pnl": equity - initial,
        "total_return_pct": (equity - initial) / initial * 100,
        "total_trades": total_trades,
        "total_wins": total_wins,
        "win_rate": total_wins / total_trades * 100 if total_trades > 0 else 0,
        "max_dd_pct": max_dd_pct * 100,
        "yearly_stats": yearly_stats,
        "monthly_pnl": monthly_pnl,
        "equity_curve": equity_curve,
    }


def main():
    print("\n" + "=" * 80)
    print("  PERSONAL ACCOUNT SIMULATION - Decorr16_A (no FTMO rules)")
    print("  Continuous compounding, anti-martingale, portfolio controls")
    print("=" * 80)

    print("\nLoading data...")
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    needed = set(ALL_COMBOS[c]["symbol"] for c in DECK)
    data_dict = {}
    for sym in needed:
        df = loader.load(sym)
        if not df.empty:
            print(f"  {sym}: {len(df):,} bars ({df.index[0].date()} -> {df.index[-1].date()})")
            data_dict[sym] = df

    streams = precompute_trades(config, instruments, data_dict)

    # Filter trades to start from 2016 (when all instruments have data)
    for combo in list(streams.keys()):
        streams[combo] = [t for t in streams[combo] if t["date"] >= date(2016, 1, 1)]

    # OOS only (2025)
    streams_oos = {}
    for combo in list(streams.keys()):
        streams_oos[combo] = [t for t in streams[combo] if t["date"] >= date(2025, 1, 1)]

    print(f"\n{'=' * 80}")
    print(f"  OOS ONLY (2025) — $10,000 start")
    print(f"{'=' * 80}")
    result_oos = simulate_personal_account(streams_oos, initial=10000)
    if result_oos:
        print(f"\n  Final equity:   ${result_oos['final_equity']:>12,.2f}")
        print(f"  Total return:   {result_oos['total_return_pct']:>11.1f}%")
        print(f"  Total trades:   {result_oos['total_trades']:>8,}")
        print(f"  Win rate:       {result_oos['win_rate']:>11.1f}%")
        print(f"  Max drawdown:   {result_oos['max_dd_pct']:>11.1f}%")
        months = sorted(result_oos["monthly_pnl"].keys())
        pos_months = sum(1 for m in months if result_oos["monthly_pnl"][m] > 0)
        print(f"  Positive months: {pos_months}/{len(months)}")
        print(f"\n  {'Month':<10s} {'PnL':>10s} {'Return':>8s}")
        print(f"  {'-'*10} {'-'*10} {'-'*8}")
        running = 10000
        for m in months:
            pnl = result_oos["monthly_pnl"][m]
            ret = pnl / running * 100
            running += pnl
            print(f"  {m:<10s} ${pnl:>+9,.2f} {ret:>+7.1f}%")

    print()
    for start_capital in [10000]:
        result = simulate_personal_account(streams, initial=start_capital)
        if result is None:
            print("No trades found!")
            return

        print(f"\n{'=' * 80}")
        print(f"  STARTING CAPITAL: ${start_capital:,}")
        print(f"{'=' * 80}")
        print(f"\n  Final equity:   ${result['final_equity']:>12,.2f}")
        print(f"  Total return:   {result['total_return_pct']:>11.1f}%")
        print(f"  Total PnL:      ${result['total_pnl']:>12,.2f}")
        print(f"  Total trades:   {result['total_trades']:>8,}")
        print(f"  Win rate:       {result['win_rate']:>11.1f}%")
        print(f"  Max drawdown:   {result['max_dd_pct']:>11.1f}%")

        years = sorted(result["yearly_stats"].keys())
        first_year = years[0]
        last_year = years[-1]
        n_years = max(1, last_year - first_year + 1)
        cagr = ((result["final_equity"] / start_capital) ** (1 / n_years) - 1) * 100

        print(f"  CAGR:           {cagr:>11.1f}%")
        print(f"  Period:         {first_year} - {last_year} ({n_years} years)")

        # Year by year
        print(f"\n  {'Year':<6s} {'Trades':>7s} {'Win%':>6s} {'PnL':>12s} {'Return':>8s} {'MaxDD':>7s} {'Equity':>12s}")
        print(f"  {'-'*6} {'-'*7} {'-'*6} {'-'*12} {'-'*8} {'-'*7} {'-'*12}")

        running_eq = start_capital
        for year in years:
            ys = result["yearly_stats"][year]
            wr = ys["wins"] / ys["trades"] * 100 if ys["trades"] > 0 else 0
            ret = ys["pnl"] / running_eq * 100 if running_eq > 0 else 0
            running_eq += ys["pnl"]
            print(f"  {year:<6d} {ys['trades']:>7d} {wr:>5.1f}% ${ys['pnl']:>+11,.2f} {ret:>+7.1f}% {ys['max_dd']*100:>6.1f}% ${running_eq:>11,.2f}")

        # Monthly summary
        months = sorted(result["monthly_pnl"].keys())
        pos_months = sum(1 for m in months if result["monthly_pnl"][m] > 0)
        neg_months = sum(1 for m in months if result["monthly_pnl"][m] <= 0)
        avg_month = sum(result["monthly_pnl"].values()) / len(months) if months else 0
        best_month = max(result["monthly_pnl"].values()) if months else 0
        worst_month = min(result["monthly_pnl"].values()) if months else 0

        print(f"\n  Monthly stats:")
        print(f"    Positive months: {pos_months}/{len(months)} ({pos_months/len(months)*100:.0f}%)")
        print(f"    Avg month:       ${avg_month:>+,.2f}")
        print(f"    Best month:      ${best_month:>+,.2f}")
        print(f"    Worst month:     ${worst_month:>+,.2f}")


if __name__ == "__main__":
    main()
