import pandas as pd


TIMEFRAME_MAP = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}

OHLCV_RULES = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "spread": "first",
}


def resample(data: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "M1":
        return data.copy()

    freq = TIMEFRAME_MAP.get(timeframe)
    if freq is None:
        raise ValueError(
            f"Unknown timeframe '{timeframe}'. Valid: {list(TIMEFRAME_MAP.keys())}"
        )

    cols = {c: rule for c, rule in OHLCV_RULES.items() if c in data.columns}
    resampled = data.resample(freq).agg(cols)

    # Drop bars where market was closed (no ticks)
    resampled = resampled.dropna(subset=["open", "close"])
    resampled = resampled[resampled["volume"] > 0]

    return resampled
