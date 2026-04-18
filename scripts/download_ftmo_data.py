"""
Download M1 historical data from FTMO MT5 for 2026 (OOS period).

Appends to existing CSV files in Datos_historicos/ or creates new ones.
Uses the FTMO_DEMO account credentials from config/accounts.yaml.

Usage:
    python -X utf8 scripts/download_ftmo_data.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from datetime import datetime, timezone
import pandas as pd
import MetaTrader5 as mt5
import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "Datos_historicos"
ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"

# Symbols in the active deck + their FTMO MT5 names
SYMBOL_MAP = {
    "EURUSD": "EURUSD",
    "GBPJPY": "GBPJPY",
    "USDCHF": "USDCHF",
    "XAUUSD": "XAUUSD",
    "XTIUSD": "USOIL.cash",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "EURJPY": "EURJPY",
}

# Instrument point sizes for spread conversion (from instruments.yaml)
POINT_SIZES = {
    "EURUSD": 0.00001,
    "GBPJPY": 0.001,
    "USDCHF": 0.00001,
    "XAUUSD": 0.01,
    "XTIUSD": 0.001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "EURJPY": 0.001,
}

# MT5 can return max ~100K bars per request, M1 for 3.5 months ≈ ~100K
# So we chunk by month to be safe.
DEFAULT_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_END = datetime.now(timezone.utc)


def load_credentials():
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    acc = raw["accounts"][0]  # FTMO_DEMO
    return acc["login"], acc["password"], acc["server"]


def download_symbol(mt5_symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Download M1 bars in monthly chunks."""
    if not mt5.symbol_select(mt5_symbol, True):
        print(f"  WARNING: could not select {mt5_symbol} in Market Watch")

    all_frames = []
    chunk_start = start

    while chunk_start < end:
        # Chunk: 30 days at a time (~43K M1 bars max)
        chunk_end = min(chunk_start + pd.Timedelta(days=30), end)

        rates = mt5.copy_rates_range(
            mt5_symbol, mt5.TIMEFRAME_M1,
            chunk_start, chunk_end
        )

        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            all_frames.append(df)
            print(f"    {chunk_start.date()} -> {chunk_end.date()}: {len(df):,} bars")
        else:
            print(f"    {chunk_start.date()} -> {chunk_end.date()}: 0 bars")

        chunk_start = chunk_end
        time.sleep(0.2)  # rate limiting

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["time"])
    combined = combined.sort_values("time").reset_index(drop=True)
    return combined


def format_for_csv(df: pd.DataFrame, internal_sym: str) -> pd.DataFrame:
    """Format MT5 data to match existing CSV structure."""
    out = pd.DataFrame()
    out["time"] = df["time"]
    out["open"] = df["open"]
    out["high"] = df["high"]
    out["low"] = df["low"]
    out["close"] = df["close"]
    out["tick_volume"] = df["tick_volume"]
    # MT5 spread is in points — keep as points (loader converts to price)
    out["spread"] = df["spread"]
    out["real_volume"] = df.get("real_volume", 0)
    return out


def find_existing_csv(symbol: str) -> Path:
    """Find existing CSV for this symbol."""
    matches = list(DATA_DIR.glob(f"{symbol}_M1_*.csv"))
    if matches:
        return matches[0]
    return DATA_DIR / f"{symbol}_M1_FTMO.csv"


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD", default=None)
    parser.add_argument("--end", help="End date YYYY-MM-DD", default=None)
    parser.add_argument("--out-suffix", help="Output file suffix (e.g. FTMO_2024)", default=None)
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.start else DEFAULT_START
    end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end else DEFAULT_END
    out_suffix = args.out_suffix

    login, password, server = load_credentials()

    print(f"Connecting to {server} (account {login})...")
    if not mt5.initialize():
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        return

    if not mt5.login(login, password=password, server=server):
        print(f"Login failed: {mt5.last_error()}")
        mt5.shutdown()
        return

    info = mt5.account_info()
    print(f"Connected: {info.login}@{info.server} (balance: {info.balance})")
    print(f"Download period: {start_date.date()} -> {end_date.date()}")
    print()

    for internal_sym, mt5_sym in SYMBOL_MAP.items():
        print(f"\n{'='*60}")
        print(f"  {internal_sym} (MT5: {mt5_sym})")
        print(f"{'='*60}")

        df = download_symbol(mt5_sym, start_date, end_date)
        if df.empty:
            print(f"  NO DATA for {mt5_sym}")
            continue

        print(f"  Total: {len(df):,} M1 bars "
              f"({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")

        formatted = format_for_csv(df, internal_sym)

        if out_suffix:
            # Separate file mode (for cross-validation)
            csv_path = DATA_DIR / f"{internal_sym}_M1_{out_suffix}.csv"
            formatted.to_csv(csv_path, index=True)
            print(f"  Created {csv_path.name} with {len(formatted):,} bars")
        else:
            # Append mode (default — adds to existing CSV)
            csv_path = find_existing_csv(internal_sym)

            if csv_path.exists():
                existing = pd.read_csv(csv_path)
                existing_times = set(existing["time"].astype(str))

                new_mask = ~formatted["time"].astype(str).isin(existing_times)
                new_bars = formatted[new_mask]

                if len(new_bars) > 0:
                    start_idx = len(existing)
                    new_bars = new_bars.reset_index(drop=True)
                    new_bars.index = range(start_idx, start_idx + len(new_bars))

                    combined = pd.concat([existing, new_bars], ignore_index=True)
                    combined.to_csv(csv_path, index=True)
                    print(f"  Appended {len(new_bars):,} new bars to {csv_path.name}")
                    print(f"  Total CSV size: {len(combined):,} bars")
                else:
                    print(f"  All bars already exist in {csv_path.name}")
            else:
                formatted.to_csv(csv_path, index=True)
                print(f"  Created {csv_path.name} with {len(formatted):,} bars")

    mt5.shutdown()
    print(f"\n{'='*60}")
    print("Done. Remember to delete parquet cache files in cache/ so the")
    print("loader picks up the new data.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
