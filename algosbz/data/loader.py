import hashlib
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from algosbz.core.config import load_instrument_config

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "Datos_historicos"
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"


class DataLoader:

    def __init__(
        self,
        data_dir: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def available_symbols(self) -> list[str]:
        files = sorted(self.data_dir.glob("*_M1_*.csv"))
        return [f.stem.split("_")[0] for f in files]

    def _find_csv(self, symbol: str) -> Path:
        pattern = f"{symbol}_M1_*.csv"
        matches = list(self.data_dir.glob(pattern))
        if not matches:
            raise FileNotFoundError(
                f"No CSV found for {symbol} in {self.data_dir} (pattern: {pattern})"
            )
        return matches[0]

    def _cache_key(self, csv_path: Path) -> str:
        stat = csv_path.stat()
        raw = f"{csv_path}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _cache_path(self, csv_path: Path) -> Path:
        key = self._cache_key(csv_path)
        return self.cache_dir / f"{csv_path.stem}_{key}.parquet"

    def load(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        csv_path = self._find_csv(symbol)
        parquet_path = self._cache_path(csv_path)

        if parquet_path.exists():
            logger.info("Loading %s from cache: %s", symbol, parquet_path.name)
            df = pd.read_parquet(parquet_path)
        else:
            logger.info("Loading %s from CSV: %s", symbol, csv_path.name)
            df = self._load_and_clean(csv_path, symbol)
            df.to_parquet(parquet_path)
            logger.info("Cached to %s", parquet_path.name)

        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]

        return df

    def _load_and_clean(self, csv_path: Path, symbol: str) -> pd.DataFrame:
        df = pd.read_csv(
            csv_path,
            index_col=0,
            parse_dates=["time"],
        )

        df = df.set_index("time")
        df.index.name = "time"

        df = df.rename(columns={"tick_volume": "volume"})
        df = df.drop(columns=["real_volume"], errors="ignore")

        # Filter to M1 bars only: remove rows where gap to next bar > 5 min
        # (catches daily/H1 bars mixed into M1 data like XAUUSD)
        if len(df) > 1:
            time_deltas = df.index.to_series().diff()
            median_delta = time_deltas.median()

            if median_delta <= pd.Timedelta(minutes=2):
                # Data is predominantly M1 — filter out non-M1 bars
                # A bar is non-M1 if the PREVIOUS gap was way too large AND
                # the NEXT gap is also large (isolated daily bars)
                # We keep all bars where at least one neighbor is ~1 min away
                fwd_deltas = df.index.to_series().diff(-1).abs()
                is_m1 = (time_deltas <= pd.Timedelta(minutes=5)) | (
                    fwd_deltas <= pd.Timedelta(minutes=5)
                )
                # Always keep the first bar
                is_m1.iloc[0] = True
                n_removed = (~is_m1).sum()
                if n_removed > 0:
                    logger.info(
                        "%s: removed %d non-M1 bars (%.1f%%)",
                        symbol, n_removed, 100 * n_removed / len(df),
                    )
                    df = df[is_m1]

        # Normalize spread from points to price units
        try:
            inst = load_instrument_config(symbol)
            df["spread"] = df["spread"] * inst.point_size
        except KeyError:
            logger.warning(
                "%s: no instrument config found, spread kept as raw points", symbol
            )

        # Drop rows with zero or negative OHLC
        mask = (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)
        n_bad = (~mask).sum()
        if n_bad > 0:
            logger.warning("%s: dropped %d rows with invalid OHLC", symbol, n_bad)
            df = df[mask]

        df = df.sort_index()
        return df
