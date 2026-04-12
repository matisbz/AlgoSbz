"""
Fresh OOS validation on 2026 FTMO MT5 data.

Downloads Jan-Apr 2026 data directly from FTMO MT5 for all instruments
in the v7 deck, then runs the exam simulation to get a true OOS funded rate
on data the optimizer has NEVER seen.

Requirements:
- MT5 terminal running and logged into FTMO
- Live bot NOT running (one MT5 connection at a time)

Usage:
    python -X utf8 scripts/oos_2026_ftmo.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import logging
import yaml
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from copy import deepcopy
from collections import defaultdict

import MetaTrader5 as mt5

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.resampler import resample
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

ROOT = Path(__file__).resolve().parent.parent

# ── Load deck ──────────────────────────────────────────────────────
from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY

# The winning deck from optimizer v7
DECK = [
    "MACross_XAUUSD_trend_H4_ny",
    "EMArib_AUDUSD_trend_H4_lon",
    "IBB_NZDUSD_trend_H4",
    "MACross_NZDUSD_trend_H4_lon",
    "MACross_XAUUSD_wideR_H4_ny",
    "Engulf_EURJPY_trend_H4",
    "Engulf_EURUSD_trend_H4",
    "SwBrk_AUDUSD_wideR_H4",
    "RSIext_EURJPY_wideR_H4",
    "MomDiv_USDCHF_trend_H4",
    "ADXbirth_GBPJPY_wideR_H4",
    "VMR_NZDUSD_wideR_H4_ny",
    "Engulf_AUDUSD_trend_H4",
    "VMR_USDCHF_default_H1_ny",
    "ADXbirth_XTIUSD_slow_ema_H4",
    "KeltSq_XAUUSD_wideR_H4_lon",
    "MACross_USDCHF_megaT_H4",
    "StrBrk_GBPJPY_wideR_H4",
    "TPB_NZDUSD_loose_H4_ny",
    "RegVMR_XAUUSD_default_H1_ny",
]

# Best exam controls from optimizer
EXAM_CONTROLS = {
    "daily_cap": 2.5,
    "cooldown": 1,
    "lookback": 0,
    "p1_rf": 1.0,
    "p2_rf": 0.7,
    "max_instr": 2,
    "max_losses": 3,
    "regime": 0,
}

# Symbol map for FTMO
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPJPY": "GBPJPY",
    "USDCHF": "USDCHF",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD",
    "XTIUSD": "USOIL.cash",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "USDCAD": "USDCAD",
    "EURJPY": "EURJPY",
}


def load_accounts():
    with open(ROOT / "config" / "accounts.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_mt5():
    accounts = load_accounts()
    acc = accounts["accounts"][0]
    print(f"Connecting to MT5: {acc['login']}@{acc['server']}")

    if not mt5.initialize():
        print(f"FATAL: mt5.initialize() failed: {mt5.last_error()}")
        sys.exit(1)
    if not mt5.login(acc["login"], password=acc["password"], server=acc["server"]):
        print(f"FATAL: mt5.login() failed: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)

    info = mt5.account_info()
    print(f"Connected: balance={info.balance:.2f}")
    return True


def download_m1(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Download M1 data from FTMO MT5."""
    mt5_sym = SYMBOL_MAP.get(symbol, symbol)

    if not mt5.symbol_select(mt5_sym, True):
        print(f"  WARN: Cannot select {mt5_sym}")
        return pd.DataFrame()

    rates = mt5.copy_rates_range(mt5_sym, mt5.TIMEFRAME_M1, start, end)
    if rates is None or len(rates) == 0:
        print(f"  WARN: No M1 data for {mt5_sym}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time")
    df = df.rename(columns={"tick_volume": "volume"})

    # Add spread column (MT5 provides it)
    if "spread" in df.columns:
        # MT5 spread is in points, convert to price difference
        info = mt5.symbol_info(mt5_sym)
        if info:
            df["spread"] = df["spread"] * info.point

    return df[["open", "high", "low", "close", "volume", "spread"]]


def load_strategy(entry):
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def precompute_trades(config, instruments, data_dict, combo_names, risk_pct=0.02):
    """Pre-compute trades (same as optimizer)."""
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
            print(f"    {combo_name}: SKIP (no data for {sym})")
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
                    "ts": ts,
                    "date": ts.date(),
                    "pnl": t.pnl,
                    "combo": combo_name,
                    "symbol": t.symbol,
                    "direction": t.direction.name,
                })
        streams[combo_name] = trades
        if trades:
            pnl_total = sum(t["pnl"] for t in trades)
            print(f"    {combo_name}: {len(trades)} trades, PnL=${pnl_total:+,.0f}")
        else:
            print(f"    {combo_name}: 0 trades")
    return streams


def simulate_exam(streams, combo_list, start_date, controls,
                  initial=100_000, p1_days=30, p2_days=60):
    """
    Simulate a single FTMO 2-step challenge.
    Uses the SAME logic as optimize_deck.py — windowed phases, proper daily DD tracking.
    Returns dict with exam result + phase details.
    """
    daily_loss_cap_pct = controls["daily_cap"]
    combo_daily_max_losses = controls["cooldown"]
    max_instr_per_day = controls["max_instr"]
    max_daily_losses = controls["max_losses"]
    p1_risk_factor = controls.get("p1_rf", 1.0)
    p2_risk_factor = controls.get("p2_rf", 0.7)

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
        for combo in combo_list:
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

            # Once target reached, stop real trading -> micro-ops for min days
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

        # If target reached but ran out of trades before 4 days,
        # check if enough weekdays remain in the window for micro-ops
        if target_reached and not locked and len(trading_days) < 4:
            days_needed = 4 - len(trading_days)
            remaining_days = 0
            check_date = target_reached_day + timedelta(days=1)
            while check_date < phase_end.date() and remaining_days < days_needed:
                if check_date.weekday() < 5:
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

    # ── Phase 1: start at $100K, target +10% ──
    p1 = run_phase(start_date, p1_days, 10.0, initial, risk_factor=p1_risk_factor)
    if p1["outcome"] != "PASS":
        return {"exam": "FAIL_P1", "p1": p1, "p2": None}

    # ── Phase 2: balance RESETS to $100K, target +5% ──
    p2_start = start_date + timedelta(days=p1["days_used"])
    p2 = run_phase(p2_start, p2_days, 5.0, initial, risk_factor=p2_risk_factor)

    if p2["outcome"] == "PASS":
        return {"exam": "FUNDED", "p1": p1, "p2": p2}
    return {"exam": "FAIL_P2", "p1": p1, "p2": p2}


def main():
    # ── Connect to FTMO MT5 ──
    connect_mt5()

    # ── Determine instruments needed ──
    symbols_needed = set()
    for combo in DECK:
        symbols_needed.add(ALL_COMBOS[combo]["symbol"])
    print(f"\nDeck: {len(DECK)} combos, {len(symbols_needed)} instruments")
    print(f"Instruments: {sorted(symbols_needed)}")

    # ── Download 2026 data ──
    # We need some history before 2026 for indicator warmup (~200 bars H4 ≈ 50 days)
    start = datetime(2025, 10, 1)  # 3 months warmup
    end = datetime(2026, 4, 13)    # today

    print(f"\nDownloading M1 data from FTMO MT5 ({start.date()} to {end.date()})...")
    data_dict = {}
    for sym in sorted(symbols_needed):
        print(f"  {sym}...", end=" ", flush=True)
        df = download_m1(sym, start, end)
        if not df.empty:
            data_dict[sym] = df
            print(f"{len(df):,} M1 bars ({df.index[0].date()} to {df.index[-1].date()})")
        else:
            print("NO DATA")

    mt5.shutdown()
    print(f"\nData loaded for {len(data_dict)}/{len(symbols_needed)} instruments")

    # ── Apply spread floor ──
    instruments = load_all_instruments()
    print("\nApplying FTMO spread floor...")
    for sym, df in data_dict.items():
        if "spread" not in df.columns:
            continue
        instr = instruments.get(sym)
        if instr is None:
            continue
        floor = instr.default_spread_pips * instr.pip_size
        before = df["spread"].mean()
        df["spread"] = df["spread"].clip(lower=floor)
        after = df["spread"].mean()
        print(f"  {sym}: floor={floor:.5f} | mean {before:.5f} -> {after:.5f}")

    # ── Pre-compute trades ──
    config = load_config()
    print(f"\nPre-computing trades on 2026 FTMO data...")
    streams = precompute_trades(config, instruments, data_dict, DECK)

    # Count 2026-only trades
    total_2026 = 0
    for combo, trades in streams.items():
        n = sum(1 for t in trades if t["date"] >= date(2026, 1, 1))
        total_2026 += n
    print(f"\nTotal trades in 2026: {total_2026}")

    # ── Run exam simulations (rolling 15-day windows in 2026) ──
    # Each exam = P1 (30 cal days) + P2 (60 cal days) = 90 days total
    # With data until Apr 10 and 90-day exams, latest start is ~Jan 10
    # We use 15-day rolling from Jan 1 to Feb 1 (enough data for full exam)
    print(f"\n{'='*80}")
    print(f"  FRESH OOS: 2026 FTMO MT5 DATA — EXAM SIMULATION")
    print(f"  Deck: Decorr20_R | Controls: {EXAM_CONTROLS}")
    print(f"  Each exam: P1=30 cal days, P2=60 cal days (same as optimizer)")
    print(f"{'='*80}\n")

    # Data ends ~Apr 10. Exam needs 90 days. Latest safe start = ~Jan 10.
    # For max windows: start every 7 days from Jan 1
    last_data_date = max(
        t["date"] for trades in streams.values() for t in trades
    ) if any(streams.values()) else date(2026, 4, 10)

    windows = []
    d = date(2026, 1, 1)
    while d + timedelta(days=90) <= last_data_date + timedelta(days=5):
        windows.append(d)
        d += timedelta(days=7)

    if not windows:
        windows = [date(2026, 1, 1)]  # at least one

    print(f"  Windows: {len(windows)} rolling starts ({windows[0]} to {windows[-1]})")
    print(f"  Data available until: {last_data_date}")
    print()

    funded = 0
    total = 0

    for start_d in windows:
        start_dt = datetime(start_d.year, start_d.month, start_d.day)
        result = simulate_exam(streams, DECK, start_dt, EXAM_CONTROLS)
        total += 1

        p1 = result["p1"]
        p2 = result["p2"]

        if result["exam"] == "FUNDED":
            funded += 1
            status = (f"FUNDED (P1: +{p1['profit_pct']:.1f}% in {p1['trading_days']}d, "
                      f"P2: +{p2['profit_pct']:.1f}% in {p2['trading_days']}d)")
        elif result["exam"] == "FAIL_P1":
            status = (f"FAIL P1: {p1['outcome']} "
                      f"(PnL: {p1['profit_pct']:+.1f}%, DD: {p1['max_dd']:.1f}%, "
                      f"DailyDD: {p1['max_daily_dd']:.1f}%, {p1['trading_days']} tdays)")
        else:  # FAIL_P2
            status = (f"P1 PASS (+{p1['profit_pct']:.1f}%) → FAIL P2: {p2['outcome']} "
                      f"(PnL: {p2['profit_pct']:+.1f}%, DD: {p2['max_dd']:.1f}%)")

        print(f"  {start_d}  →  {status}")

    rate = funded / total * 100 if total else 0
    print(f"\n{'='*80}")
    print(f"  RESULTS: {funded}/{total} funded = {rate:.1f}%")
    print(f"{'='*80}")

    print(f"\n  Comparison:")
    print(f"    IS  (2016-2024, 107 windows): 45.8%")
    print(f"    OOS (2025, 7 windows):        28.6%")
    print(f"    FRESH 2026 ({total} windows):    {rate:.1f}%")

    if rate >= 35:
        print(f"\n  VERDICT: VIABLE — fresh OOS confirms edge")
    elif rate >= 20:
        print(f"\n  VERDICT: BORDERLINE — edge exists but weaker than IS suggests")
    else:
        print(f"\n  VERDICT: WEAK — edge not confirmed on fresh data")


if __name__ == "__main__":
    main()
