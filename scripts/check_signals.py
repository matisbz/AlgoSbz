"""
Check what signals the Decorr16_A deck should have generated on a given date.
Uses MT5 to download recent data and runs each strategy.

Usage:
    python scripts/check_signals.py [--date 2026-04-01]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import importlib
import pandas as pd
from datetime import datetime, timedelta

from algosbz.live.mt5_connector import MT5Connector
from algosbz.core.enums import SignalAction
from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY

# Decorr16_A deck
DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
    "StrBrk_GBPJPY_slow_H4", "EMArib_XNGUSD_loose_H4", "SMCOB_XAUUSD_H4",
    "SwBrk_SPY_fast_H4",
]

# Account config for MT5 connection
ACCOUNT = {
    "login": 1512964593,
    "password": "nIQHl?m7",
    "server": "FTMO-Demo",
}

SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPJPY": "GBPJPY",
    "USDCHF": "USDCHF",
    "USDJPY": "USDJPY",
    "XAUUSD": "XAUUSD",
    "XTIUSD": "USOIL.cash",
    "XNGUSD": "NATGAS.cash",
    "SPY": "US500.cash",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="Date to check (YYYY-MM-DD). Default: yesterday")
    args = parser.parse_args()

    if args.date:
        check_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        check_date = (datetime.now() - timedelta(days=1)).date()

    print(f"\n  Checking signals for: {check_date}")
    print(f"  Deck: {len(DECK)} combos (Decorr16_A)\n")

    # Connect to MT5
    conn = MT5Connector(ACCOUNT["login"], ACCOUNT["password"],
                        ACCOUNT["server"], SYMBOL_MAP)
    if not conn.connect():
        print("ERROR: Could not connect to MT5")
        return

    # Group combos by (symbol, timeframe) to avoid duplicate downloads
    feeds = {}  # (symbol, tf) -> [combo_names]
    for combo in DECK:
        entry = ALL_COMBOS[combo]
        tf = entry["params"].get("timeframe", "H4")
        key = (entry["symbol"], tf)
        feeds.setdefault(key, []).append(combo)

    # Download data for each feed
    bar_data = {}
    for (symbol, tf), combos in feeds.items():
        print(f"  Downloading {symbol} {tf}...")
        df = conn.get_bars(symbol, tf, 500)
        if df.empty:
            print(f"    ERROR: No data for {symbol} {tf}")
            continue
        bar_data[(symbol, tf)] = df
        print(f"    {len(df)} bars, range: {df.index[0]} -> {df.index[-1]}")

    conn.disconnect()
    print()

    # Run each strategy and check for signals on the target date
    signals_found = []
    no_bars_on_date = []

    for combo in DECK:
        entry = ALL_COMBOS[combo]
        tf = entry["params"].get("timeframe", "H4")
        key = (entry["symbol"], tf)

        if key not in bar_data:
            print(f"  {combo:<30s}  NO DATA")
            continue

        df = bar_data[key]

        # Find bars on the check_date
        bars_on_date = df[df.index.date == check_date]
        if bars_on_date.empty:
            no_bars_on_date.append(combo)
            continue

        # Load and setup strategy
        info = STRAT_REGISTRY[entry["strat"]]
        mod = importlib.import_module(info["module"])
        cls = getattr(mod, info["class"])
        strategy = cls(entry["params"])
        strategy.setup(df)

        # Run on_bar for each bar on the target date
        combo_signals = []
        for idx in range(len(df)):
            bar = df.iloc[idx]
            bar_time = df.index[idx]

            if bar_time.date() != check_date:
                # Still need to call on_bar to maintain state
                if bar_time.date() < check_date:
                    strategy.on_bar(idx, bar, False)
                continue

            signal = strategy.on_bar(idx, bar, False)
            if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                direction = "BUY" if signal.action == SignalAction.ENTER_LONG else "SELL"
                combo_signals.append({
                    "time": bar_time,
                    "direction": direction,
                    "sl": signal.stop_loss,
                    "tp": signal.take_profit,
                })

        if combo_signals:
            for s in combo_signals:
                tp_str = f"{s['tp']:.5f}" if s['tp'] else "None"
                print(f"  {'>>':>4s} {combo:<30s}  {s['direction']:>4s}  "
                      f"@ {s['time']}  SL={s['sl']:.5f}  TP={tp_str}")
                signals_found.append((combo, s))
        else:
            print(f"  {'--':>4s} {combo:<30s}  no signal")

    if no_bars_on_date:
        print(f"\n  No bars on {check_date} for: {', '.join(no_bars_on_date)}")

    print(f"\n  SUMMARY: {len(signals_found)} signals on {check_date}")
    if not signals_found:
        print("  (No trading signals — this is normal, deck averages ~1.5 trades/day)")


if __name__ == "__main__":
    main()
