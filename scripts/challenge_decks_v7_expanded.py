"""
Clean combo pool v7 — 53 combos from massive scan.
Signal-deduplicated: 39 redundant combos removed.
Threshold: subset>80%, clone>90%
Generated: 2026-04-12
"""

STRAT_REGISTRY = {
    "ADXbirth": {"module": "algosbz.strategy.adx_trend_birth", "class": "ADXTrendBirth"},
    "CCIext": {"module": "algosbz.strategy.cci_extreme", "class": "CCIExtreme"},
    "EMArib": {"module": "algosbz.strategy.ema_ribbon_trend", "class": "EMARibbonTrend"},
    "Engulf": {"module": "algosbz.strategy.engulfing_reversal", "class": "EngulfingReversal"},
    "IBB": {"module": "algosbz.strategy.inside_bar_breakout", "class": "InsideBarBreakout"},
    "KeltSq": {"module": "algosbz.strategy.keltner_squeeze", "class": "KeltnerSqueeze"},
    "MACDhist": {"module": "algosbz.strategy.macd_histogram", "class": "MACDHistogram"},
    "MACross": {"module": "algosbz.strategy.ma_crossover", "class": "MACrossover"},
    "MomDiv": {"module": "algosbz.strategy.momentum_divergence", "class": "MomentumDivergence"},
    "PinBar": {"module": "algosbz.strategy.pin_bar", "class": "PinBarReversal"},
    "RSIext": {"module": "algosbz.strategy.rsi_extreme", "class": "RSIExtreme"},
    "RegVMR": {"module": "algosbz.strategy.regime_vmr", "class": "RegimeAdaptiveVMR"},
    "StochRev": {"module": "algosbz.strategy.stochastic_reversal", "class": "StochasticReversal"},
    "StrBrk": {"module": "algosbz.strategy.structure_break", "class": "StructureBreak"},
    "SwBrk": {"module": "algosbz.strategy.swing_breakout", "class": "SwingBreakout"},
    "TPB": {"module": "algosbz.strategy.trend_pullback", "class": "TrendPullback"},
    "VMR": {"module": "algosbz.strategy.volatility_mean_reversion", "class": "VolatilityMeanReversion"},
}

ALL_COMBOS = {
    "MACross_XAUUSD_trend_H4_ny": {
        "strat": "MACross", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 2.85,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 12, 'session_end': 21},
    },
    "EMArib_AUDUSD_trend_H4_lon": {
        "strat": "EMArib", "symbol": "AUDUSD", "tier": "ROBUST", "pf": 2.46,
        "params": {'timeframe': 'H4', 'ribbon_threshold': 0.85, 'ribbon_confirm_bars': 4, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 7, 'session_end': 16},
    },
    "IBB_NZDUSD_trend_H4": {
        "strat": "IBB", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 2.29,
        "params": {'timeframe': 'H4', 'min_inside_bars': 2, 'trend_ema': 100, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "MACross_NZDUSD_trend_H4_lon": {
        "strat": "MACross", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 2.26,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 7, 'session_end': 16},
    },
    "MACross_XAUUSD_wideR_H4_ny": {
        "strat": "MACross", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 2.07,
        "params": {'timeframe': 'H4', 'fast_period': 8, 'slow_period': 21, 'adx_min': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 12, 'session_end': 21},
    },
    "Engulf_EURJPY_trend_H4": {
        "strat": "Engulf", "symbol": "EURJPY", "tier": "ROBUST", "pf": 2.03,
        "params": {'timeframe': 'H4', 'swing_zone_atr': 0.4, 'min_body_ratio': 0.8, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "SwBrk_EURJPY_slow_H4": {
        "strat": "SwBrk", "symbol": "EURJPY", "tier": "SPREAD_OK", "pf": 1.78,
        "params": {'timeframe': 'H4', 'donchian_period': 30, 'squeeze_pct': 0.75, 'tp_atr_mult': 4.0, 'session_start': 0, 'session_end': 23},
    },
    "TPB_XTIUSD_trend_H4_ny": {
        "strat": "TPB", "symbol": "XTIUSD", "tier": "SPREAD_OK", "pf": 1.77,
        "params": {'timeframe': 'H4', 'adx_min': 28, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 12, 'session_end': 21},
    },
    "Engulf_EURUSD_trend_H4": {
        "strat": "Engulf", "symbol": "EURUSD", "tier": "ROBUST", "pf": 1.68,
        "params": {'timeframe': 'H4', 'swing_zone_atr': 0.4, 'min_body_ratio': 0.8, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "SwBrk_AUDUSD_wideR_H4": {
        "strat": "SwBrk", "symbol": "AUDUSD", "tier": "ROBUST", "pf": 1.55,
        "params": {'timeframe': 'H4', 'donchian_period': 25, 'squeeze_pct': 0.8, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 0, 'session_end': 23},
    },
    "ADXbirth_GBPJPY_wideR_H4": {
        "strat": "ADXbirth", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.5,
        "params": {'timeframe': 'H4', 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 0, 'session_end': 23},
    },
    "RSIext_EURJPY_wideR_H4": {
        "strat": "RSIext", "symbol": "EURJPY", "tier": "ROBUST", "pf": 1.47,
        "params": {'timeframe': 'H4', 'rsi_oversold': 20, 'rsi_overbought': 80, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 0, 'session_end': 23},
    },
    "MomDiv_USDCHF_trend_H4": {
        "strat": "MomDiv", "symbol": "USDCHF", "tier": "ROBUST", "pf": 1.46,
        "params": {'timeframe': 'H4', 'min_rsi_diff': 7, 'divergence_window': 25, 'swing_lookback': 6, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "VMR_NZDUSD_wideR_H4_ny": {
        "strat": "VMR", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 1.45,
        "params": {'timeframe': 'H4', 'bb_std': 2.5, 'consec_outside': 2, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.0, 'session_start': 12, 'session_end': 21},
    },
    "MACross_GBPJPY_megaT_H4": {
        "strat": "MACross", "symbol": "GBPJPY", "tier": "SPREAD_OK", "pf": 1.41,
        "params": {'timeframe': 'H4', 'fast_period': 21, 'slow_period': 55, 'adx_min': 22, 'sl_atr_mult': 2.5, 'tp_atr_mult': 7.0, 'session_start': 0, 'session_end': 23},
    },
    "SwBrk_XTIUSD_slow_H4": {
        "strat": "SwBrk", "symbol": "XTIUSD", "tier": "SPREAD_OK", "pf": 1.41,
        "params": {'timeframe': 'H4', 'donchian_period': 30, 'squeeze_pct': 0.75, 'tp_atr_mult': 4.0, 'session_start': 0, 'session_end': 23},
    },
    "EMArib_EURJPY_tight_H1": {
        "strat": "EMArib", "symbol": "EURJPY", "tier": "SPREAD_OK", "pf": 1.38,
        "params": {'timeframe': 'H1', 'ribbon_threshold': 0.9, 'ribbon_confirm_bars': 5, 'session_start': 0, 'session_end': 23},
    },
    "VMR_USDCHF_default_H1_ny": {
        "strat": "VMR", "symbol": "USDCHF", "tier": "ROBUST", "pf": 1.38,
        "params": {'timeframe': 'H1', 'session_start': 12, 'session_end': 21},
    },
    "ADXbirth_XTIUSD_slow_ema_H4": {
        "strat": "ADXbirth", "symbol": "XTIUSD", "tier": "ROBUST", "pf": 1.37,
        "params": {'timeframe': 'H4', 'ema_period': 100, 'lookback_low': 10, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.5, 'session_start': 0, 'session_end': 23},
    },
    "KeltSq_XAUUSD_wideR_H4_lon": {
        "strat": "KeltSq", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.37,
        "params": {'timeframe': 'H4', 'squeeze_bars': 4, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 7, 'session_end': 16},
    },
    "StrBrk_GBPJPY_wideR_H4": {
        "strat": "StrBrk", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.37,
        "params": {'timeframe': 'H4', 'swing_lookback': 6, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.5, 'session_start': 0, 'session_end': 23},
    },
    "Engulf_AUDUSD_trend_H4": {
        "strat": "Engulf", "symbol": "AUDUSD", "tier": "ROBUST", "pf": 1.36,
        "params": {'timeframe': 'H4', 'swing_zone_atr': 0.4, 'min_body_ratio': 0.8, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "TPB_NZDUSD_loose_H4_ny": {
        "strat": "TPB", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 1.35,
        "params": {'timeframe': 'H4', 'adx_min': 20, 'pullback_zone_atr': 0.7, 'sl_atr_mult': 2.5, 'tp_atr_mult': 4.0, 'session_start': 12, 'session_end': 21},
    },
    "MACross_USDCHF_megaT_H4": {
        "strat": "MACross", "symbol": "USDCHF", "tier": "ROBUST", "pf": 1.34,
        "params": {'timeframe': 'H4', 'fast_period': 21, 'slow_period': 55, 'adx_min': 22, 'sl_atr_mult': 2.5, 'tp_atr_mult': 7.0, 'session_start': 0, 'session_end': 23},
    },
    "TPB_NZDUSD_trendL_H4": {
        "strat": "TPB", "symbol": "NZDUSD", "tier": "SPREAD_OK", "pf": 1.34,
        "params": {'timeframe': 'H4', 'adx_min': 22, 'sl_atr_mult': 2.5, 'tp_atr_mult': 7.0, 'session_start': 0, 'session_end': 23},
    },
    "KeltSq_AUDUSD_wideR_H4_lon": {
        "strat": "KeltSq", "symbol": "AUDUSD", "tier": "SPREAD_OK", "pf": 1.33,
        "params": {'timeframe': 'H4', 'squeeze_bars': 4, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 7, 'session_end': 16},
    },
    "ADXbirth_GBPJPY_sensitive_H4": {
        "strat": "ADXbirth", "symbol": "GBPJPY", "tier": "ROBUST", "pf": 1.31,
        "params": {'timeframe': 'H4', 'adx_low': 22, 'adx_trigger': 22, 'lookback_low': 10, 'sl_atr_mult': 2.0, 'tp_atr_mult': 4.5, 'session_start': 0, 'session_end': 23},
    },
    "TPB_NZDUSD_loose_H4_lon": {
        "strat": "TPB", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 1.31,
        "params": {'timeframe': 'H4', 'adx_min': 20, 'pullback_zone_atr': 0.7, 'sl_atr_mult': 2.5, 'tp_atr_mult': 4.0, 'session_start': 7, 'session_end': 16},
    },
    "CCIext_USDCAD_extreme_H4": {
        "strat": "CCIext", "symbol": "USDCAD", "tier": "SPREAD_OK", "pf": 1.3,
        "params": {'timeframe': 'H4', 'cci_extreme': 250, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.5, 'session_start': 0, 'session_end': 23},
    },
    "EMArib_USDCHF_trend_H4_lon": {
        "strat": "EMArib", "symbol": "USDCHF", "tier": "ROBUST", "pf": 1.29,
        "params": {'timeframe': 'H4', 'ribbon_threshold': 0.85, 'ribbon_confirm_bars': 4, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 7, 'session_end': 16},
    },
    "RegVMR_XAUUSD_default_H1_ny": {
        "strat": "RegVMR", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.29,
        "params": {'timeframe': 'H1', 'session_start': 12, 'session_end': 21},
    },
    "EMArib_USDJPY_trend_H4": {
        "strat": "EMArib", "symbol": "USDJPY", "tier": "SPREAD_OK", "pf": 1.28,
        "params": {'timeframe': 'H4', 'ribbon_threshold': 0.85, 'ribbon_confirm_bars': 4, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 0, 'session_end': 23},
    },
    "RegVMR_NZDUSD_default_H1_ny": {
        "strat": "RegVMR", "symbol": "NZDUSD", "tier": "ROBUST", "pf": 1.27,
        "params": {'timeframe': 'H1', 'session_start': 12, 'session_end': 21},
    },
    "ADXbirth_USDCAD_default_H4": {
        "strat": "ADXbirth", "symbol": "USDCAD", "tier": "ROBUST", "pf": 1.23,
        "params": {'timeframe': 'H4', 'session_start': 0, 'session_end': 23},
    },
    "MACross_EURUSD_wideR_H4_lon": {
        "strat": "MACross", "symbol": "EURUSD", "tier": "ROBUST", "pf": 1.23,
        "params": {'timeframe': 'H4', 'fast_period': 8, 'slow_period': 21, 'adx_min': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 7, 'session_end': 16},
    },
    "MACross_USDCHF_trend_H4_ny": {
        "strat": "MACross", "symbol": "USDCHF", "tier": "SPREAD_OK", "pf": 1.23,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 12, 'session_end': 21},
    },
    "MACross_AUDUSD_megaT_H4": {
        "strat": "MACross", "symbol": "AUDUSD", "tier": "SPREAD_OK", "pf": 1.22,
        "params": {'timeframe': 'H4', 'fast_period': 21, 'slow_period': 55, 'adx_min': 22, 'sl_atr_mult': 2.5, 'tp_atr_mult': 7.0, 'session_start': 0, 'session_end': 23},
    },
    "PinBar_EURJPY_deep_H4": {
        "strat": "PinBar", "symbol": "EURJPY", "tier": "SPREAD_OK", "pf": 1.21,
        "params": {'timeframe': 'H4', 'level_lookback': 30, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.5, 'session_start': 0, 'session_end': 23},
    },
    "RSIext_USDCHF_wideR_H1": {
        "strat": "RSIext", "symbol": "USDCHF", "tier": "SPREAD_OK", "pf": 1.19,
        "params": {'timeframe': 'H1', 'rsi_oversold': 20, 'rsi_overbought': 80, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 0, 'session_end': 23},
    },
    "MACross_USDJPY_wideR_H4_lon": {
        "strat": "MACross", "symbol": "USDJPY", "tier": "SPREAD_OK", "pf": 1.18,
        "params": {'timeframe': 'H4', 'fast_period': 8, 'slow_period': 21, 'adx_min': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 7, 'session_end': 16},
    },
    "MomDiv_AUDUSD_wideR_H4": {
        "strat": "MomDiv", "symbol": "AUDUSD", "tier": "ROBUST", "pf": 1.18,
        "params": {'timeframe': 'H4', 'min_rsi_diff': 5, 'divergence_window': 30, 'swing_lookback': 5, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 0, 'session_end': 23},
    },
    "VMR_XAUUSD_wideR_H4_ny": {
        "strat": "VMR", "symbol": "XAUUSD", "tier": "ROBUST", "pf": 1.18,
        "params": {'timeframe': 'H4', 'bb_std': 2.5, 'consec_outside': 2, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.0, 'session_start': 12, 'session_end': 21},
    },
    "MACross_USDCHF_wideR_H4_lon": {
        "strat": "MACross", "symbol": "USDCHF", "tier": "SPREAD_OK", "pf": 1.17,
        "params": {'timeframe': 'H4', 'fast_period': 8, 'slow_period': 21, 'adx_min': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 7, 'session_end': 16},
    },
    "StrBrk_USDJPY_trend_H4": {
        "strat": "StrBrk", "symbol": "USDJPY", "tier": "SPREAD_OK", "pf": 1.16,
        "params": {'timeframe': 'H4', 'swing_lookback': 8, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.5, 'session_start': 0, 'session_end': 23},
    },
    "MACDhist_EURJPY_trend_H4": {
        "strat": "MACDhist", "symbol": "EURJPY", "tier": "ROBUST", "pf": 1.15,
        "params": {'timeframe': 'H4', 'adx_min': 20, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'hist_threshold_atr': 0.4, 'session_start': 0, 'session_end': 23},
    },
    "VMR_USDJPY_wideR_H4_ny": {
        "strat": "VMR", "symbol": "USDJPY", "tier": "SPREAD_OK", "pf": 1.14,
        "params": {'timeframe': 'H4', 'bb_std': 2.5, 'consec_outside': 2, 'sl_atr_mult': 2.5, 'tp_atr_mult': 5.0, 'session_start': 12, 'session_end': 21},
    },
    "Engulf_XAUUSD_tight_H4": {
        "strat": "Engulf", "symbol": "XAUUSD", "tier": "SPREAD_OK", "pf": 1.13,
        "params": {'timeframe': 'H4', 'swing_zone_atr': 0.3, 'min_body_ratio': 0.7, 'tp_atr_mult': 3.0, 'session_start': 0, 'session_end': 23},
    },
    "StochRev_AUDUSD_calm_H4": {
        "strat": "StochRev", "symbol": "AUDUSD", "tier": "ROBUST", "pf": 1.13,
        "params": {'timeframe': 'H4', 'adx_max': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 4.5, 'session_start': 0, 'session_end': 23},
    },
    "MACross_USDCAD_trend_H4_lon": {
        "strat": "MACross", "symbol": "USDCAD", "tier": "SPREAD_OK", "pf": 1.1,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 7, 'session_end': 16},
    },
    "MACross_USDJPY_trend_H4_ny": {
        "strat": "MACross", "symbol": "USDJPY", "tier": "ROBUST", "pf": 1.08,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 12, 'session_end': 21},
    },
    "RegVMR_XTIUSD_default_H1": {
        "strat": "RegVMR", "symbol": "XTIUSD", "tier": "SPREAD_OK", "pf": 1.08,
        "params": {'timeframe': 'H1', 'session_start': 0, 'session_end': 23},
    },
    "MACross_EURUSD_wideR_H4_ny": {
        "strat": "MACross", "symbol": "EURUSD", "tier": "ROBUST", "pf": 1.06,
        "params": {'timeframe': 'H4', 'fast_period': 8, 'slow_period': 21, 'adx_min': 25, 'sl_atr_mult': 2.0, 'tp_atr_mult': 5.0, 'session_start': 12, 'session_end': 21},
    },
    "MACross_XTIUSD_trend_H4_ny": {
        "strat": "MACross", "symbol": "XTIUSD", "tier": "SPREAD_OK", "pf": 1.06,
        "params": {'timeframe': 'H4', 'fast_period': 12, 'slow_period': 34, 'adx_min': 25, 'sl_atr_mult': 2.5, 'tp_atr_mult': 6.0, 'session_start': 12, 'session_end': 21},
    },
}
