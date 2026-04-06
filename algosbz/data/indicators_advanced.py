"""
Advanced indicators for professional-grade strategies.
Market structure, order blocks, FVG, sessions, volatility regime.
"""
import numpy as np
import pandas as pd

from algosbz.data.indicators import atr, ema


# ─── Market Structure ──────────────────────────────────────────────

def swing_points(
    high: pd.Series, low: pd.Series, left: int = 5, right: int = 5
) -> pd.DataFrame:
    """Detect swing highs and swing lows using left/right bar comparison.

    Swings are marked at the confirmation bar (i + right), not at the pivot bar,
    to avoid look-ahead bias. The stored value is still the pivot price.
    """
    n = len(high)
    swing_high = pd.Series(np.nan, index=high.index)
    swing_low = pd.Series(np.nan, index=low.index)

    for i in range(left, n - right):
        # Swing high: highest in [i-left, i+right], confirmed at i+right
        if high.iloc[i] == high.iloc[i - left: i + right + 1].max():
            swing_high.iloc[i + right] = high.iloc[i]
        # Swing low: lowest in [i-left, i+right], confirmed at i+right
        if low.iloc[i] == low.iloc[i - left: i + right + 1].min():
            swing_low.iloc[i + right] = low.iloc[i]

    return pd.DataFrame({"swing_high": swing_high, "swing_low": swing_low}, index=high.index)


def structure_breaks(
    high: pd.Series, low: pd.Series, swing_left: int = 5, swing_right: int = 5
) -> pd.DataFrame:
    """
    Detect Break of Structure (BOS) and Change of Character (CHoCH).

    BOS: trend continuation (higher high in uptrend, lower low in downtrend)
    CHoCH: trend reversal (lower low after uptrend, higher high after downtrend)
    """
    swings = swing_points(high, low, swing_left, swing_right)
    n = len(high)

    bos_bull = pd.Series(False, index=high.index)
    bos_bear = pd.Series(False, index=high.index)
    choch_bull = pd.Series(False, index=high.index)
    choch_bear = pd.Series(False, index=high.index)

    last_swing_high = np.nan
    last_swing_low = np.nan
    prev_swing_high = np.nan
    prev_swing_low = np.nan
    trend = 0  # 1 = bullish, -1 = bearish, 0 = undefined

    for i in range(n):
        sh = swings["swing_high"].iloc[i]
        sl = swings["swing_low"].iloc[i]

        if not np.isnan(sh):
            prev_swing_high = last_swing_high
            last_swing_high = sh

        if not np.isnan(sl):
            prev_swing_low = last_swing_low
            last_swing_low = sl

        if np.isnan(last_swing_high) or np.isnan(last_swing_low):
            continue

        # Check for breaks using close
        close_val = high.iloc[i]  # use high for bullish breaks

        if not np.isnan(prev_swing_high) and high.iloc[i] > last_swing_high:
            if trend == 1 or trend == 0:
                bos_bull.iloc[i] = True
                trend = 1
            elif trend == -1:
                choch_bull.iloc[i] = True
                trend = 1

        if not np.isnan(prev_swing_low) and low.iloc[i] < last_swing_low:
            if trend == -1 or trend == 0:
                bos_bear.iloc[i] = True
                trend = -1
            elif trend == 1:
                choch_bear.iloc[i] = True
                trend = -1

    return pd.DataFrame({
        "bos_bull": bos_bull,
        "bos_bear": bos_bear,
        "choch_bull": choch_bull,
        "choch_bear": choch_bear,
    }, index=high.index)


# ─── Order Blocks ──────────────────────────────────────────────────

def order_blocks(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series,
    swing_left: int = 5, swing_right: int = 5, max_age_bars: int = 500
) -> pd.DataFrame:
    """
    Detect order blocks: last opposing candle before a structure break.

    Bullish OB: last bearish candle before a BOS up (demand zone)
    Bearish OB: last bullish candle before a BOS down (supply zone)

    Returns active (unmitigated) OB zones at each bar.
    """
    breaks = structure_breaks(high, low, swing_left, swing_right)
    n = len(high)

    # Track active OBs as list of dicts
    active_bull_obs = []  # {"top": float, "bottom": float, "bar_idx": int}
    active_bear_obs = []

    ob_bull_top = pd.Series(np.nan, index=high.index)
    ob_bull_bottom = pd.Series(np.nan, index=high.index)
    ob_bear_top = pd.Series(np.nan, index=high.index)
    ob_bear_bottom = pd.Series(np.nan, index=high.index)

    for i in range(n):
        # On bullish BOS, find the last bearish candle before it
        if breaks["bos_bull"].iloc[i] or breaks["choch_bull"].iloc[i]:
            for j in range(i - 1, max(0, i - 30) - 1, -1):
                if close.iloc[j] < open_.iloc[j]:  # bearish candle
                    active_bull_obs.append({
                        "top": high.iloc[j],
                        "bottom": low.iloc[j],
                        "bar_idx": i,
                    })
                    break

        # On bearish BOS, find the last bullish candle
        if breaks["bos_bear"].iloc[i] or breaks["choch_bear"].iloc[i]:
            for j in range(i - 1, max(0, i - 30) - 1, -1):
                if close.iloc[j] > open_.iloc[j]:  # bullish candle
                    active_bear_obs.append({
                        "top": high.iloc[j],
                        "bottom": low.iloc[j],
                        "bar_idx": i,
                    })
                    break

        # Mitigate OBs: remove if price passes through
        active_bull_obs = [
            ob for ob in active_bull_obs
            if low.iloc[i] >= ob["bottom"] and (i - ob["bar_idx"]) < max_age_bars
        ]
        active_bear_obs = [
            ob for ob in active_bear_obs
            if high.iloc[i] <= ob["top"] and (i - ob["bar_idx"]) < max_age_bars
        ]

        # Check if price is AT an OB zone
        price = close.iloc[i]
        for ob in active_bull_obs:
            if ob["bottom"] <= price <= ob["top"]:
                ob_bull_top.iloc[i] = ob["top"]
                ob_bull_bottom.iloc[i] = ob["bottom"]
                break

        for ob in active_bear_obs:
            if ob["bottom"] <= price <= ob["top"]:
                ob_bear_top.iloc[i] = ob["top"]
                ob_bear_bottom.iloc[i] = ob["bottom"]
                break

    return pd.DataFrame({
        "ob_bull_top": ob_bull_top,
        "ob_bull_bottom": ob_bull_bottom,
        "ob_bear_top": ob_bear_top,
        "ob_bear_bottom": ob_bear_bottom,
    }, index=high.index)


# ─── Fair Value Gaps ──────────────────────────────────────────────

def fair_value_gaps(
    high: pd.Series, low: pd.Series,
    min_gap_atr_ratio: float = 0.3, atr_period: int = 14,
    close: pd.Series = None,
) -> pd.DataFrame:
    """
    Detect Fair Value Gaps (imbalances).

    Bullish FVG: candle[i-2].high < candle[i].low (gap up)
    Bearish FVG: candle[i-2].low > candle[i].high (gap down)
    """
    n = len(high)
    fvg_bull_top = pd.Series(np.nan, index=high.index)
    fvg_bull_bottom = pd.Series(np.nan, index=high.index)
    fvg_bear_top = pd.Series(np.nan, index=high.index)
    fvg_bear_bottom = pd.Series(np.nan, index=high.index)

    if close is None:
        close = (high + low) / 2

    atr_vals = atr(high, low, close, atr_period)

    # Track active FVGs
    active_bull_fvgs = []
    active_bear_fvgs = []

    for i in range(2, n):
        current_atr = atr_vals.iloc[i]
        if np.isnan(current_atr) or current_atr <= 0:
            continue

        # Bullish FVG: gap between candle[i-2] high and candle[i] low
        gap_up = low.iloc[i] - high.iloc[i - 2]
        if gap_up > current_atr * min_gap_atr_ratio:
            active_bull_fvgs.append({
                "top": low.iloc[i],
                "bottom": high.iloc[i - 2],
            })

        # Bearish FVG: gap between candle[i] high and candle[i-2] low
        gap_down = low.iloc[i - 2] - high.iloc[i]
        if gap_down > current_atr * min_gap_atr_ratio:
            active_bear_fvgs.append({
                "top": low.iloc[i - 2],
                "bottom": high.iloc[i],
            })

        # Mitigate filled FVGs
        price = close.iloc[i]
        active_bull_fvgs = [f for f in active_bull_fvgs if price >= f["bottom"]]
        active_bear_fvgs = [f for f in active_bear_fvgs if price <= f["top"]]

        # Check if price is at a FVG
        for fvg in active_bull_fvgs:
            if fvg["bottom"] <= price <= fvg["top"]:
                fvg_bull_top.iloc[i] = fvg["top"]
                fvg_bull_bottom.iloc[i] = fvg["bottom"]
                break

        for fvg in active_bear_fvgs:
            if fvg["bottom"] <= price <= fvg["top"]:
                fvg_bear_top.iloc[i] = fvg["top"]
                fvg_bear_bottom.iloc[i] = fvg["bottom"]
                break

    return pd.DataFrame({
        "fvg_bull_top": fvg_bull_top,
        "fvg_bull_bottom": fvg_bull_bottom,
        "fvg_bear_top": fvg_bear_top,
        "fvg_bear_bottom": fvg_bear_bottom,
    }, index=high.index)


# ─── Volume Profile (tick volume proxy) ──────────────────────────

def vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series,
    session_reset: bool = True,
) -> pd.Series:
    """Session-resetting VWAP using typical price."""
    tp = (high + low + close) / 3
    tpv = tp * volume

    if session_reset:
        dates = pd.Series(close.index.date, index=close.index)
        cum_tpv = tpv.groupby(dates).cumsum()
        cum_vol = volume.groupby(dates).cumsum()
    else:
        cum_tpv = tpv.cumsum()
        cum_vol = volume.cumsum()

    return cum_tpv / cum_vol.replace(0, np.nan)


# ─── Sessions / Kill Zones ──────────────────────────────────────

KILL_ZONES = {
    "london_kz": (7, 10),    # 07:00-10:00 UTC
    "ny_kz": (12, 15),       # 12:00-15:00 UTC
    "asia": (0, 8),
    "london": (7, 16),
    "new_york": (12, 21),
    "overlap": (12, 16),
}


def session_labels(index: pd.DatetimeIndex) -> pd.Series:
    """Categorize each bar by session."""
    hours = index.hour
    labels = pd.Series("off_hours", index=index)

    labels[(hours >= 0) & (hours < 8)] = "asia"
    labels[(hours >= 7) & (hours < 10)] = "london_open"
    labels[(hours >= 12) & (hours < 15)] = "ny_open"
    labels[(hours >= 12) & (hours < 16)] = "overlap"

    return labels


def kill_zone_mask(index: pd.DatetimeIndex) -> pd.Series:
    """Boolean: True during London 07-10 or NY 12-15 UTC."""
    hours = index.hour
    london = (hours >= 7) & (hours < 10)
    ny = (hours >= 12) & (hours < 15)
    return pd.Series(london | ny, index=index)


# ─── Volatility Regime ──────────────────────────────────────────

def atr_percentile(
    high: pd.Series, low: pd.Series, close: pd.Series,
    atr_period: int = 14, lookback: int = 100,
) -> pd.Series:
    """Current ATR as percentile of its own distribution over lookback."""
    atr_vals = atr(high, low, close, atr_period)

    def pctl(window):
        if len(window) < 2:
            return 50.0
        current = window.iloc[-1]
        rank = (window < current).sum()
        return rank / (len(window) - 1) * 100

    return atr_vals.rolling(lookback, min_periods=20).apply(pctl, raw=False)


def volatility_regime(
    high: pd.Series, low: pd.Series, close: pd.Series,
    atr_period: int = 14, lookback: int = 100,
) -> pd.Series:
    """Categorical volatility: low, normal, high, extreme."""
    pctl = atr_percentile(high, low, close, atr_period, lookback)
    regime = pd.Series("normal", index=high.index)
    regime[pctl < 25] = "low"
    regime[(pctl >= 25) & (pctl < 75)] = "normal"
    regime[(pctl >= 75) & (pctl < 95)] = "high"
    regime[pctl >= 95] = "extreme"
    return regime


# ─── Trend Strength ──────────────────────────────────────────────

def trend_strength_composite(
    close: pd.Series, high: pd.Series, low: pd.Series,
    ema_fast: int = 8, ema_slow: int = 21, adx_period: int = 14,
) -> pd.Series:
    """
    Composite trend score from -100 to +100.
    Combines EMA slope, price position, and momentum.
    """
    from algosbz.data.indicators import adx as compute_adx

    ema_f = ema(close, ema_fast)
    ema_s = ema(close, ema_slow)

    # EMA alignment: fast above slow = bullish
    ema_diff = (ema_f - ema_s) / ema_s * 100
    ema_score = ema_diff.clip(-2, 2) * 25  # normalize to -50 to +50

    # ADX contribution: strength magnitude
    adx_vals = compute_adx(high, low, close, adx_period)
    adx_score = adx_vals.clip(0, 50) / 50 * 50  # 0-50

    # Combine: direction from EMA, magnitude from ADX
    score = ema_score.copy()
    # Amplify score when ADX confirms
    for i in range(len(score)):
        if not np.isnan(adx_score.iloc[i]):
            direction = 1 if score.iloc[i] > 0 else -1 if score.iloc[i] < 0 else 0
            score.iloc[i] = direction * (abs(score.iloc[i]) * 0.5 + adx_score.iloc[i] * 0.5)

    return score.clip(-100, 100)


def ema_ribbon_score(
    close: pd.Series, periods: list[int] = None,
) -> pd.Series:
    """
    EMA ribbon alignment score.
    +1.0 = perfectly aligned bullish (all EMAs in order fast > slow)
    -1.0 = perfectly aligned bearish
    """
    if periods is None:
        periods = [8, 13, 21, 34, 55]

    emas = [ema(close, p) for p in periods]
    n = len(close)
    scores = np.zeros(n)

    for i in range(n):
        vals = [e.iloc[i] for e in emas]
        if any(np.isnan(v) for v in vals):
            scores[i] = 0
            continue

        # Count correctly ordered pairs
        total_pairs = 0
        correct_pairs = 0
        for a in range(len(vals)):
            for b in range(a + 1, len(vals)):
                total_pairs += 1
                if vals[a] > vals[b]:
                    correct_pairs += 1

        # -1 to +1
        if total_pairs > 0:
            bullish_score = correct_pairs / total_pairs  # 0 to 1
            bearish_test = sum(1 for a in range(len(vals)) for b in range(a + 1, len(vals)) if vals[a] < vals[b])
            bearish_score = bearish_test / total_pairs

            scores[i] = bullish_score - bearish_score

    return pd.Series(scores, index=close.index)
