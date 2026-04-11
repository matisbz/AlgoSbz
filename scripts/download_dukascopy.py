"""
Download M1 OHLCV from Dukascopy and save as CSVs in the format the project's
DataLoader expects.

CSV format expected by algosbz/data/loader.py:
    ,time,open,high,low,close,tick_volume,spread,real_volume
    0,2010-01-04 00:00:00,1.4302,1.4456,1.4257,1.4412,10981,20,0

Notes:
- Dukascopy fetch returns OHLCV with UTC datetime index. We rename and reformat.
- spread is set to 0 — the massive_scan applies a realistic FTMO spread floor
  via instrument.default_spread_pips, so the bar spread doesn't matter.
- We fetch in monthly chunks to stay within rate limits and recover gracefully.

Usage:
    python -X utf8 scripts/download_dukascopy.py
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import dukascopy_python as duka
import dukascopy_python.instruments as dinstr


# ── Symbols to download ─────────────────────────────────────────────
# Maps our internal symbol → (dukascopy fetch string, dukascopy constant)
# We use the fetch string form (e.g. "AUD/USD") which is what fetch() accepts.
SYMBOLS = {
    "AUDUSD": "AUD/USD",
    "NZDUSD": "NZD/USD",
    "USDCAD": "USD/CAD",
    "EURJPY": "EUR/JPY",
}

START_YEAR = 2015
END_DATE = datetime(2026, 1, 1)

OUT_DIR = Path(__file__).resolve().parent.parent / "Datos_historicos"
OUT_DIR.mkdir(exist_ok=True)


def month_chunks(start: datetime, end: datetime):
    """Yield (chunk_start, chunk_end) tuples month by month."""
    cur = start
    while cur < end:
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1)
        if nxt > end:
            nxt = end
        yield cur, nxt
        cur = nxt


def fetch_symbol(symbol_internal: str, fetch_str: str) -> pd.DataFrame:
    """Fetch all M1 bars for one symbol in monthly chunks."""
    out_path = OUT_DIR / f"{symbol_internal}_M1_Dukascopy.csv"
    if out_path.exists():
        existing = pd.read_csv(out_path)
        print(f"  [{symbol_internal}] already exists ({len(existing):,} rows) — SKIP")
        return None

    print(f"\n=== {symbol_internal} ({fetch_str}) ===")
    chunks = list(month_chunks(datetime(START_YEAR, 1, 1), END_DATE))
    print(f"  Fetching {len(chunks)} monthly chunks...")

    all_dfs = []
    failures = 0
    t0 = time.time()

    for i, (cstart, cend) in enumerate(chunks, 1):
        try:
            df = duka.fetch(
                fetch_str,
                duka.INTERVAL_MIN_1,
                duka.OFFER_SIDE_BID,
                cstart,
                cend,
                max_retries=5,
            )
            if df is None or df.empty:
                print(f"  [{i}/{len(chunks)}] {cstart.date()} — empty")
                continue
            all_dfs.append(df)
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta_min = (len(chunks) - i) / rate / 60 if rate > 0 else 0
            print(f"  [{i}/{len(chunks)}] {cstart.date()} — {len(df):,} bars "
                  f"({rate:.1f} ch/s, ETA {eta_min:.1f}m)")
        except Exception as e:
            failures += 1
            print(f"  [{i}/{len(chunks)}] {cstart.date()} — FAIL: {e}")
            if failures > 10:
                print(f"  Too many failures, aborting {symbol_internal}")
                return None
            time.sleep(2)

    if not all_dfs:
        print(f"  [{symbol_internal}] no data fetched")
        return None

    df = pd.concat(all_dfs).sort_index()
    # Drop duplicates from chunk overlap
    df = df[~df.index.duplicated(keep="first")]

    # Reformat to project CSV layout
    out = pd.DataFrame({
        "time": df.index.tz_convert(None) if df.index.tz else df.index,
        "open": df["open"].values,
        "high": df["high"].values,
        "low": df["low"].values,
        "close": df["close"].values,
        "tick_volume": df["volume"].astype("int64").values,
        "spread": 0,             # will be overridden by FTMO floor in massive_scan
        "real_volume": 0,
    })
    out.to_csv(out_path, index=True, index_label="")
    print(f"  -> {out_path.name}: {len(out):,} rows, "
          f"{out['time'].min()} → {out['time'].max()}")
    return out


def main():
    print(f"Dukascopy M1 download")
    print(f"  Date range: {START_YEAR}-01-01 → {END_DATE.date()}")
    print(f"  Symbols:    {', '.join(SYMBOLS.keys())}")
    print(f"  Output:     {OUT_DIR}")

    for sym_internal, sym_fetch in SYMBOLS.items():
        try:
            fetch_symbol(sym_internal, sym_fetch)
        except KeyboardInterrupt:
            print("\n[interrupted]")
            return
        except Exception as e:
            print(f"\n[{sym_internal}] FATAL: {e}")
            continue

    print("\nDone.")


if __name__ == "__main__":
    main()
