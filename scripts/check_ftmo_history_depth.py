"""
Check how far back FTMO MT5 data goes for each instrument and timeframe.

NOTE: Requires the live bot to be stopped (only one MT5 connection at a time).

Usage:
    python -X utf8 scripts/check_ftmo_history_depth.py
"""
import sys
from datetime import datetime
from pathlib import Path

import yaml
import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent

SYMBOLS = ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD",
           "AUDUSD", "NZDUSD", "USDCAD", "EURJPY"]

TIMEFRAMES = [
    ("M1",  mt5.TIMEFRAME_M1),
    ("M5",  mt5.TIMEFRAME_M5),
    ("M15", mt5.TIMEFRAME_M15),
    ("H1",  mt5.TIMEFRAME_H1),
    ("H4",  mt5.TIMEFRAME_H4),
    ("D1",  mt5.TIMEFRAME_D1),
]


def load_accounts():
    with open(ROOT / "config" / "accounts.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
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
    print(f"Server time: {datetime.now()}\n")

    # Header
    tf_names = [name for name, _ in TIMEFRAMES]
    print(f"{'Symbol':<12}" + "".join(f"{tf:<22}" for tf in tf_names))
    print("-" * (12 + 22 * len(TIMEFRAMES)))

    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print(f"{sym:<12} CANNOT SELECT")
            continue

        row = f"{sym:<12}"
        for tf_name, tf_val in TIMEFRAMES:
            # Request a large number of bars backwards from now
            # MT5 will return whatever it has available
            rates = mt5.copy_rates_from_pos(sym, tf_val, 0, 50000)
            if rates is not None and len(rates) > 0:
                first = datetime.fromtimestamp(rates[0]["time"])
                # last bar is most recent (position 0 = oldest available)
                row += f"{first.strftime('%Y-%m-%d'):<14}({len(rates):>7})"
            else:
                row += f"{'NO DATA':<22}"
        print(row)

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
