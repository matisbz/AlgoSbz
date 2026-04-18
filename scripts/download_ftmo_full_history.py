"""
Download full M1 history from FTMO MT5 for all deck instruments.

Replaces Darwinex/Dukascopy data with FTMO broker data to eliminate
data source divergence in backtests.

Downloads in monthly chunks to stay under MT5's 100K bar limit.
Saves to Datos_historicos/{SYMBOL}_M1_FTMO_full.csv

Usage:
    python -X utf8 scripts/download_ftmo_full_history.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from datetime import datetime, timezone, timedelta
import pandas as pd
import MetaTrader5 as mt5
import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "Datos_historicos"
ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"

SYMBOLS = {
    "EURUSD": {"mt5": "EURUSD", "start": 2015},
    "GBPJPY": {"mt5": "GBPJPY", "start": 2015},
    "USDCHF": {"mt5": "USDCHF", "start": 2015},
    "USDJPY": {"mt5": "USDJPY", "start": 2015},
    "XAUUSD": {"mt5": "XAUUSD", "start": 2015},
    "XTIUSD": {"mt5": "USOIL.cash", "start": 2021},
    "AUDUSD": {"mt5": "AUDUSD", "start": 2015},
    "NZDUSD": {"mt5": "NZDUSD", "start": 2015},
    "EURJPY": {"mt5": "EURJPY", "start": 2015},
    "USDCAD": {"mt5": "USDCAD", "start": 2015},
}


def download_symbol(mt5_symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Download M1 bars in monthly chunks."""
    mt5.symbol_select(mt5_symbol, True)
    all_frames = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=28), end)
        rates = mt5.copy_rates_range(mt5_symbol, mt5.TIMEFRAME_M1, chunk_start, chunk_end)

        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            all_frames.append(df)
            bars = len(df)
        else:
            bars = 0

        # Print progress every 6 months
        if chunk_start.month in (1, 7) and chunk_start.day <= 28:
            print(f"    {chunk_start.strftime('%Y-%m')}... {bars} bars", flush=True)

        chunk_start = chunk_end
        time.sleep(0.1)

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"])
    combined = combined.sort_values("time").reset_index(drop=True)
    return combined


def main():
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    acc = raw["accounts"][0]

    print(f"Connecting to {acc['server']}...")
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        return
    if not mt5.login(acc["login"], password=acc["password"], server=acc["server"]):
        print(f"Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    info = mt5.account_info()
    print(f"Connected: {info.login}@{info.server}")
    end_date = datetime.now(timezone.utc)

    for internal_sym, cfg in SYMBOLS.items():
        out_path = DATA_DIR / f"{internal_sym}_M1_FTMO_full.csv"

        # Skip if already downloaded
        if out_path.exists():
            existing = pd.read_csv(out_path)
            print(f"\n{internal_sym}: already exists ({len(existing):,} bars) — SKIP")
            continue

        start = datetime(cfg["start"], 1, 1, tzinfo=timezone.utc)
        mt5_sym = cfg["mt5"]

        print(f"\n{'='*60}")
        print(f"  {internal_sym} (MT5: {mt5_sym}) — {cfg['start']} to now")
        print(f"{'='*60}")

        df = download_symbol(mt5_sym, start, end_date)
        if df.empty:
            print(f"  NO DATA")
            continue

        # Format output
        out = pd.DataFrame()
        out["time"] = df["time"]
        out["open"] = df["open"]
        out["high"] = df["high"]
        out["low"] = df["low"]
        out["close"] = df["close"]
        out["tick_volume"] = df["tick_volume"]
        out["spread"] = df["spread"]
        out["real_volume"] = df.get("real_volume", 0)

        out.to_csv(out_path, index=True)
        print(f"  SAVED: {out_path.name} — {len(out):,} bars "
              f"({out['time'].iloc[0]} -> {out['time'].iloc[-1]})")

    mt5.shutdown()
    print(f"\n{'='*60}")
    print("Done. Delete cache/*.parquet so loader picks up new data.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
