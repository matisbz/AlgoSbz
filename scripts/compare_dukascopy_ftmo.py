"""
Compare Dukascopy CSVs vs FTMO MT5 live data for the last ~2 months.

Quantifies bar-level differences (OHLC pip diffs, missing bars, gap structure)
to validate whether Dukascopy data is suitable for the backtest of new instruments
that we'd then trade live on FTMO.

Run while the live bot is stopped (only one MT5 terminal connection at a time).

Usage:
    python -X utf8 scripts/compare_dukascopy_ftmo.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

import MetaTrader5 as mt5


SYMBOLS = ["AUDUSD", "NZDUSD", "USDCAD", "EURJPY"]
PIP_SIZE = {"AUDUSD": 0.0001, "NZDUSD": 0.0001, "USDCAD": 0.0001, "EURJPY": 0.01}
# Compare overlap: Dukascopy ends 2025-12-31, take last 2 months of available data
RANGE_START = datetime(2025, 11, 1)
RANGE_END = datetime(2026, 1, 1)

ROOT = Path(__file__).resolve().parent.parent
DUKA_DIR = ROOT / "Datos_historicos"


def load_accounts():
    with open(ROOT / "config" / "accounts.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_ftmo(symbol: str) -> pd.DataFrame:
    # Pad the range so we still hit the target window after the broker→UTC shift
    rates = mt5.copy_rates_range(
        symbol, mt5.TIMEFRAME_M1,
        RANGE_START - timedelta(days=1), RANGE_END + timedelta(days=1),
    )
    if rates is None or len(rates) == 0:
        print(f"  [{symbol}] no MT5 data: {mt5.last_error()}")
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time").sort_index()
    # FTMO MT5 server time = EET (UTC+2 winter, UTC+3 summer DST).
    # Nov-Dec is winter -> shift -2h to convert to UTC and align with Dukascopy.
    df.index = df.index - pd.Timedelta(hours=2)
    df = df[(df.index >= RANGE_START) & (df.index < RANGE_END)]
    return df[["open", "high", "low", "close", "tick_volume", "spread"]]


def load_duka(symbol: str) -> pd.DataFrame:
    p = DUKA_DIR / f"{symbol}_M1_Dukascopy.csv"
    df = pd.read_csv(p, usecols=["time", "open", "high", "low", "close", "tick_volume"],
                     parse_dates=["time"])
    df = df.set_index("time").sort_index()
    return df


def compare(symbol: str, duka: pd.DataFrame, ftmo: pd.DataFrame):
    pip = PIP_SIZE[symbol]
    print(f"\n=== {symbol} ===")
    print(f"  duka bars: {len(duka):,}  range: {duka.index.min()} -> {duka.index.max()}")
    print(f"  ftmo bars: {len(ftmo):,}  range: {ftmo.index.min()} -> {ftmo.index.max()}")

    # Inner join on timestamp
    j = duka.join(ftmo, how="inner", lsuffix="_d", rsuffix="_f")
    if j.empty:
        print("  no overlap!")
        return

    # OHLC diffs in pips
    for col in ["open", "high", "low", "close"]:
        d = (j[f"{col}_d"] - j[f"{col}_f"]).abs() / pip
        print(f"  |Δ{col}| pips:  mean={d.mean():.3f}  p50={d.quantile(0.5):.3f}  "
              f"p95={d.quantile(0.95):.3f}  max={d.max():.2f}")

    # Coverage stats
    only_duka = duka.index.difference(ftmo.index)
    only_ftmo = ftmo.index.difference(duka.index)
    common = duka.index.intersection(ftmo.index)
    print(f"  common bars:    {len(common):,}")
    print(f"  only in duka:   {len(only_duka):,}")
    print(f"  only in ftmo:   {len(only_ftmo):,}")
    if len(duka):
        print(f"  duka coverage:  {len(common)/len(duka)*100:.2f}% of duka in ftmo")
    if len(ftmo):
        print(f"  ftmo coverage:  {len(common)/len(ftmo)*100:.2f}% of ftmo in duka")

    # Spread sanity (ftmo only)
    if "spread" in ftmo.columns:
        info = mt5.symbol_info(symbol)
        if info:
            spread_pips = ftmo["spread"] * info.point / pip
            print(f"  ftmo spread:    median={spread_pips.median():.2f} pips  "
                  f"p95={spread_pips.quantile(0.95):.2f}  max={spread_pips.max():.2f}")


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
    print(f"Connected: balance={info.balance:.2f}\n")

    print(f"Comparison range: {RANGE_START} -> {RANGE_END}\n")

    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print(f"\n[{sym}] cannot select symbol on FTMO — skipping")
            continue
        ftmo = fetch_ftmo(sym)
        duka_full = load_duka(sym)
        duka = duka_full[(duka_full.index >= RANGE_START) & (duka_full.index < RANGE_END)]
        compare(sym, duka, ftmo)

    mt5.shutdown()
    print("\nDone.")


if __name__ == "__main__":
    main()
