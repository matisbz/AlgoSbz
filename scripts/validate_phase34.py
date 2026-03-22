"""
Run Phase 3 (spread stress) and Phase 4 (param sensitivity) on the 26 candidates
that passed Phases 1-2 from the massive scan.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import logging
import pandas as pd
from copy import deepcopy

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# The 26 combos that passed Phase 1 (PF>1.05) and Phase 2 (3/5 periods)
CANDIDATES = [
    {"combo": "VMR_USDCHF_default_H1", "strat": "VMR", "symbol": "USDCHF", "pf": 1.30, "wr": 52.0, "trades": 396, "periods": 3,
     "params": {"timeframe": "H1"}},
    {"combo": "VMR_USDJPY_default_H4", "strat": "VMR", "symbol": "USDJPY", "pf": 1.07, "wr": 46.4, "trades": 69, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "VMR_SPY_default_H4", "strat": "VMR", "symbol": "SPY", "pf": 1.13, "wr": 46.4, "trades": 69, "periods": 4,
     "params": {"timeframe": "H4"}},
    {"combo": "TPB_XTIUSD_default_H4", "strat": "TPB", "symbol": "XTIUSD", "pf": 1.09, "wr": 52.2, "trades": 67, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "TPB_GBPJPY_loose_H1", "strat": "TPB", "symbol": "GBPJPY", "pf": 1.06, "wr": 46.0, "trades": 100, "periods": 4,
     "params": {"timeframe": "H1", "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0}},
    {"combo": "TPB_XTIUSD_loose_H4", "strat": "TPB", "symbol": "XTIUSD", "pf": 1.13, "wr": 48.0, "trades": 100, "periods": 4,
     "params": {"timeframe": "H4", "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0}},
    {"combo": "TPB_XNGUSD_loose_H4", "strat": "TPB", "symbol": "XNGUSD", "pf": 1.10, "wr": 46.0, "trades": 80, "periods": 3,
     "params": {"timeframe": "H4", "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0}},
    {"combo": "SwBrk_XTIUSD_default_H4", "strat": "SwBrk", "symbol": "XTIUSD", "pf": 1.29, "wr": 40.9, "trades": 44, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "SwBrk_SPY_default_H4", "strat": "SwBrk", "symbol": "SPY", "pf": 1.05, "wr": 36.4, "trades": 22, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "SwBrk_SPY_fast_H4", "strat": "SwBrk", "symbol": "SPY", "pf": 1.10, "wr": 38.0, "trades": 40, "periods": 3,
     "params": {"timeframe": "H4", "donchian_period": 10, "squeeze_pct": 0.85, "adx_min": 15}},
    {"combo": "SwBrk_SPY_slow_H4", "strat": "SwBrk", "symbol": "SPY", "pf": 1.08, "wr": 37.0, "trades": 30, "periods": 3,
     "params": {"timeframe": "H4", "donchian_period": 30, "squeeze_pct": 0.75, "tp_atr_mult": 4.0}},
    {"combo": "IBB_EURUSD_loose_H4", "strat": "IBB", "symbol": "EURUSD", "pf": 1.06, "wr": 42.0, "trades": 50, "periods": 3,
     "params": {"timeframe": "H4", "min_bar_range_pct": 0.2, "sl_atr_mult": 2.0, "tp_atr_mult": 4.0}},
    {"combo": "Engulf_EURUSD_default_H4", "strat": "Engulf", "symbol": "EURUSD", "pf": 1.08, "wr": 41.1, "trades": 192, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "Engulf_EURUSD_tight_H4", "strat": "Engulf", "symbol": "EURUSD", "pf": 1.15, "wr": 44.0, "trades": 100, "periods": 4,
     "params": {"timeframe": "H4", "swing_zone_atr": 0.3, "min_body_ratio": 0.7, "tp_atr_mult": 3.0}},
    {"combo": "Engulf_XAUUSD_tight_H4", "strat": "Engulf", "symbol": "XAUUSD", "pf": 1.12, "wr": 43.0, "trades": 80, "periods": 4,
     "params": {"timeframe": "H4", "swing_zone_atr": 0.3, "min_body_ratio": 0.7, "tp_atr_mult": 3.0}},
    {"combo": "StrBrk_GBPJPY_slow_H4", "strat": "StrBrk", "symbol": "GBPJPY", "pf": 1.10, "wr": 42.0, "trades": 60, "periods": 4,
     "params": {"timeframe": "H4", "swing_lookback": 7, "tp_atr_mult": 4.0}},
    {"combo": "MomDiv_SPY_default_H1", "strat": "MomDiv", "symbol": "SPY", "pf": 1.14, "wr": 43.6, "trades": 172, "periods": 3,
     "params": {"timeframe": "H1"}},
    {"combo": "MomDiv_SPY_loose_H1", "strat": "MomDiv", "symbol": "SPY", "pf": 1.10, "wr": 42.0, "trades": 200, "periods": 3,
     "params": {"timeframe": "H1", "min_rsi_diff": 2, "divergence_window": 40, "swing_lookback": 3}},
    {"combo": "RegVMR_XAUUSD_default_H1", "strat": "RegVMR", "symbol": "XAUUSD", "pf": 1.25, "wr": 50.0, "trades": 36, "periods": 3,
     "params": {"timeframe": "H1"}},
    {"combo": "RegVMR_XTIUSD_default_H1", "strat": "RegVMR", "symbol": "XTIUSD", "pf": 1.10, "wr": 45.0, "trades": 40, "periods": 3,
     "params": {"timeframe": "H1"}},
    {"combo": "EMArib_XNGUSD_loose_H4", "strat": "EMArib", "symbol": "XNGUSD", "pf": 1.15, "wr": 40.0, "trades": 60, "periods": 3,
     "params": {"timeframe": "H4", "ribbon_threshold": 0.5, "ribbon_confirm_bars": 2, "rsi_pullback_bull": 50, "rsi_pullback_bear": 50}},
    {"combo": "SessBrk_XTIUSD_default_M15", "strat": "SessBrk", "symbol": "XTIUSD", "pf": 2.01, "wr": 55.8, "trades": 77, "periods": 4,
     "params": {"timeframe": "M15"}},
    {"combo": "SMCOB_GBPJPY_default_H1", "strat": "SMCOB", "symbol": "GBPJPY", "pf": 1.10, "wr": 40.1, "trades": 397, "periods": 3,
     "params": {"timeframe": "H1"}},
    {"combo": "SMCOB_XAUUSD_default_H4", "strat": "SMCOB", "symbol": "XAUUSD", "pf": 1.29, "wr": 40.9, "trades": 66, "periods": 3,
     "params": {"timeframe": "H4"}},
    {"combo": "SMCOB_XAUUSD_loose_H4", "strat": "SMCOB", "symbol": "XAUUSD", "pf": 1.49, "wr": 48.6, "trades": 105, "periods": 3,
     "params": {"timeframe": "H4", "rejection_wick_ratio": 0.4, "tp_atr_mult": 2.5}},
    {"combo": "SMCOB_GBPJPY_tight_H1", "strat": "SMCOB", "symbol": "GBPJPY", "pf": 1.09, "wr": 40.4, "trades": 272, "periods": 4,
     "params": {"timeframe": "H1", "rejection_wick_ratio": 0.6, "sl_atr_mult": 1.0, "tp_atr_mult": 2.0}},
]

STRATEGIES = {
    "VMR": {"module": "algosbz.strategy.volatility_mean_reversion", "class": "VolatilityMeanReversion"},
    "TPB": {"module": "algosbz.strategy.trend_pullback", "class": "TrendPullback"},
    "SwBrk": {"module": "algosbz.strategy.swing_breakout", "class": "SwingBreakout"},
    "IBB": {"module": "algosbz.strategy.inside_bar_breakout", "class": "InsideBarBreakout"},
    "Engulf": {"module": "algosbz.strategy.engulfing_reversal", "class": "EngulfingReversal"},
    "StrBrk": {"module": "algosbz.strategy.structure_break", "class": "StructureBreak"},
    "MomDiv": {"module": "algosbz.strategy.momentum_divergence", "class": "MomentumDivergence"},
    "KSqz": {"module": "algosbz.strategy.keltner_squeeze", "class": "KeltnerSqueeze"},
    "RegVMR": {"module": "algosbz.strategy.regime_vmr", "class": "RegimeAdaptiveVMR"},
    "EMArib": {"module": "algosbz.strategy.ema_ribbon_trend", "class": "EMARibbonTrend"},
    "SessBrk": {"module": "algosbz.strategy.session_breakout_v2", "class": "SessionBreakout"},
    "SMCOB": {"module": "algosbz.strategy.smc_order_block", "class": "SMCOrderBlock"},
    "FVGrev": {"module": "algosbz.strategy.fvg_reversion", "class": "FVGReversion"},
    "VWAPrev": {"module": "algosbz.strategy.vwap_reversion", "class": "VWAPReversion"},
    "H4MR": {"module": "algosbz.strategy.h4_mean_reversion", "class": "H4MeanReversion"},
}


def load_strategy(strat_key, preset_params):
    info = STRATEGIES[strat_key]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    params = {"session_start": 0, "session_end": 23, **preset_params}
    return cls(params)


def run_backtest(config, instrument_cfg, data, strat_key, preset_params, symbol,
                 spread_mult=1.0, min_trades=10):
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    if spread_mult != 1.0:
        instrument_cfg = instrument_cfg.model_copy(update={
            "default_spread_pips": instrument_cfg.default_spread_pips * spread_mult
        })

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )
    try:
        strategy = load_strategy(strat_key, preset_params)
        engine = BacktestEngine(cfg, instrument_cfg, EquityManager(eq_cfg))
        result = engine.run(strategy, data, symbol)
    except Exception as e:
        return None

    if result.total_trades < min_trades:
        return None

    return {
        "trades": result.total_trades,
        "pf": round(result.profit_factor, 2),
    }


def param_sensitivity(config, instrument_cfg, data, strat_key, preset_params, symbol, base_pf):
    sensitive_keys = ["sl_atr_mult", "tp_atr_mult"]
    worst_pf = base_pf
    worst_label = ""

    for key in sensitive_keys:
        if key not in preset_params:
            info = STRATEGIES[strat_key]
            mod = importlib.import_module(info["module"])
            cls = getattr(mod, info["class"])
            default_val = cls.DEFAULT_PARAMS.get(key)
            if default_val is None:
                continue
            val = default_val
        else:
            val = preset_params[key]

        for mult in [0.8, 1.2]:
            variant = {**preset_params, key: val * mult}
            r = run_backtest(config, instrument_cfg, data, strat_key, variant, symbol,
                             min_trades=5)
            if r is None:
                continue
            if r["pf"] < worst_pf:
                worst_pf = r["pf"]
                worst_label = f"{key}×{mult}"

    return worst_pf, worst_label


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Load data for needed symbols
    needed_symbols = set(c["symbol"] for c in CANDIDATES)
    data_cache = {}
    print("Loading data...")
    for sym in needed_symbols:
        data_cache[sym] = loader.load(sym, start="2015-01-01", end="2025-01-01")
        print(f"  {sym}: {len(data_cache[sym]):,} bars")

    # First, re-run base PF to get accurate numbers
    print(f"\n{'='*100}")
    print(f"  RE-RUNNING BASE PF FOR ALL 26 CANDIDATES")
    print(f"{'='*100}\n")

    for c in CANDIDATES:
        r = run_backtest(config, instruments[c["symbol"]], data_cache[c["symbol"]],
                         c["strat"], c["params"], c["symbol"], min_trades=5)
        if r:
            c["pf"] = r["pf"]
            c["trades"] = r["trades"]
            print(f"  {c['combo']:45s} PF={r['pf']:.2f} T={r['trades']}")
        else:
            c["pf"] = 0
            print(f"  {c['combo']:45s} FAILED")

    # Filter out any that now fail
    active = [c for c in CANDIDATES if c["pf"] >= 1.05]
    print(f"\n  Active after re-check: {len(active)}")

    # ── PHASE 3: Spread stress ────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 3: Spread stress (+50%)")
    print(f"{'='*100}\n")

    spread_ok = []
    for c in active:
        print(f"  {c['combo']:45s}...", end=" ", flush=True)
        r = run_backtest(config, instruments[c["symbol"]], data_cache[c["symbol"]],
                         c["strat"], c["params"], c["symbol"], spread_mult=1.5, min_trades=5)
        if r and r["pf"] > 1.0:
            print(f"PASS stress_PF={r['pf']:.2f}")
            c["stress_pf"] = r["pf"]
            spread_ok.append(c)
        else:
            spf = r["pf"] if r else 0
            print(f"FAIL stress_PF={spf}")

    print(f"\n  Phase 3: {len(spread_ok)} passed")

    # ── PHASE 4: Parameter sensitivity ────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 4: Param sensitivity (±20% SL/TP)")
    print(f"{'='*100}\n")

    robust = []
    sensitivity_marginal = []
    for c in spread_ok:
        print(f"  {c['combo']:45s}...", end=" ", flush=True)
        worst_pf, worst_label = param_sensitivity(
            config, instruments[c["symbol"]], data_cache[c["symbol"]],
            c["strat"], c["params"], c["symbol"], c["pf"])
        if worst_pf > 1.0:
            print(f"PASS worst={worst_pf:.2f} ({worst_label})")
            c["worst_pf"] = worst_pf
            c["tier"] = "ROBUST"
            robust.append(c)
        else:
            print(f"FAIL worst={worst_pf:.2f} ({worst_label})")
            c["worst_pf"] = worst_pf
            c["tier"] = "SPREAD_OK"
            sensitivity_marginal.append(c)

    # ── RESULTS ───────────────────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  FINAL VALIDATED PORTFOLIO")
    print(f"{'='*100}\n")

    print(f"  ROBUST (all 4 tests): {len(robust)}")
    for c in sorted(robust, key=lambda x: x["pf"], reverse=True):
        print(f"    {c['combo']:45s} PF={c['pf']:.2f} T={c['trades']:4d} "
              f"Per={c['periods']}/5 StPF={c['stress_pf']:.2f} WrstPF={c['worst_pf']:.2f}")

    print(f"\n  SPREAD_OK (spread passed, sensitivity marginal): {len(sensitivity_marginal)}")
    for c in sorted(sensitivity_marginal, key=lambda x: x["pf"], reverse=True):
        print(f"    {c['combo']:45s} PF={c['pf']:.2f} T={c['trades']:4d} "
              f"Per={c['periods']}/5 StPF={c['stress_pf']:.2f} WrstPF={c['worst_pf']:.2f}")

    all_viable = robust + sensitivity_marginal
    if all_viable:
        total_t = sum(c["trades"] for c in all_viable)
        avg_pf = sum(c["pf"] * c["trades"] for c in all_viable) / total_t
        unique_strats = len(set(c["strat"] for c in all_viable))
        unique_syms = len(set(c["symbol"] for c in all_viable))
        print(f"\n  Portfolio: {len(all_viable)} combos, {unique_strats} strategies, {unique_syms} instruments")
        print(f"  Total trades: {total_t} ({total_t/119:.1f}/month)")
        print(f"  Weighted PF: {avg_pf:.2f}")

    # Save
    Path("cache").mkdir(exist_ok=True)
    rows = [{k: v for k, v in c.items() if k != "params"} for c in all_viable]
    if rows:
        pd.DataFrame(rows).to_csv("cache/massive_scan_results.csv", index=False)
        print(f"\n  Saved to cache/massive_scan_results.csv")


if __name__ == "__main__":
    main()
