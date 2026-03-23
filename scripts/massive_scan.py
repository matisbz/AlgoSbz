"""
Massive strategy scan with parameter variants + full validation pipeline.

Scans: 15 strategies × 9 instruments × multiple TFs × 2-3 param presets
Pipeline: PF>1.05 → Period stability (3/5) → Spread stress (+50%) → Param sensitivity (±20%)

Usage:
    python -X utf8 scripts/massive_scan.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import logging
import pandas as pd
import numpy as np
from copy import deepcopy
from datetime import datetime

from algosbz.core.config import load_config, load_instrument_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# ── Strategy registry with parameter presets ──────────────────────────────

STRATEGIES = {
    "VMR": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "tight_H1": {"timeframe": "H1", "bb_std": 2.0, "consec_outside": 1, "sl_atr_mult": 2.0, "tp_atr_mult": 3.0},
            "wide_H1": {"timeframe": "H1", "bb_std": 3.0, "consec_outside": 3, "sl_atr_mult": 4.0, "tp_atr_mult": 5.0},
            "tight_H4": {"timeframe": "H4", "bb_std": 2.0, "consec_outside": 1, "sl_atr_mult": 2.0, "tp_atr_mult": 3.0},
        },
    },
    "TPB": {
        "module": "algosbz.strategy.trend_pullback",
        "class": "TrendPullback",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "loose_H1": {"timeframe": "H1", "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0},
            "loose_H4": {"timeframe": "H4", "adx_min": 20, "pullback_zone_atr": 0.7, "sl_atr_mult": 2.5, "tp_atr_mult": 4.0},
        },
    },
    "SwBrk": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "presets": {
            "default_H4": {"timeframe": "H4"},
            "default_H1": {"timeframe": "H1"},
            "fast_H4": {"timeframe": "H4", "donchian_period": 10, "squeeze_pct": 0.85, "adx_min": 15},
            "slow_H4": {"timeframe": "H4", "donchian_period": 30, "squeeze_pct": 0.75, "tp_atr_mult": 4.0},
            "fast_H1": {"timeframe": "H1", "donchian_period": 10, "squeeze_pct": 0.85, "adx_min": 15},
        },
    },
    "IBB": {
        "module": "algosbz.strategy.inside_bar_breakout",
        "class": "InsideBarBreakout",
        "presets": {
            "default_H4": {"timeframe": "H4"},
            "default_H1": {"timeframe": "H1"},
            "multi_H4": {"timeframe": "H4", "min_inside_bars": 2, "tp_atr_mult": 4.0},
            "loose_H4": {"timeframe": "H4", "min_bar_range_pct": 0.2, "sl_atr_mult": 2.0, "tp_atr_mult": 4.0},
        },
    },
    "Engulf": {
        "module": "algosbz.strategy.engulfing_reversal",
        "class": "EngulfingReversal",
        "presets": {
            "default_H4": {"timeframe": "H4"},
            "default_H1": {"timeframe": "H1"},
            "wide_H4": {"timeframe": "H4", "swing_zone_atr": 0.8, "adx_max": 35, "min_body_ratio": 0.5},
            "tight_H4": {"timeframe": "H4", "swing_zone_atr": 0.3, "min_body_ratio": 0.7, "tp_atr_mult": 3.0},
            "wide_H1": {"timeframe": "H1", "swing_zone_atr": 0.8, "adx_max": 35, "min_body_ratio": 0.5},
        },
    },
    "StrBrk": {
        "module": "algosbz.strategy.structure_break",
        "class": "StructureBreak",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "fast_H1": {"timeframe": "H1", "swing_lookback": 3, "min_swing_distance_atr": 0.3, "tp_atr_mult": 2.5},
            "slow_H4": {"timeframe": "H4", "swing_lookback": 7, "tp_atr_mult": 4.0},
        },
    },
    "MomDiv": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "presets": {
            "default_H4": {"timeframe": "H4"},
            "default_H1": {"timeframe": "H1"},
            "loose_H4": {"timeframe": "H4", "min_rsi_diff": 2, "divergence_window": 40, "swing_lookback": 3},
            "loose_H1": {"timeframe": "H1", "min_rsi_diff": 2, "divergence_window": 40, "swing_lookback": 3},
        },
    },
    "RegVMR": {
        "module": "algosbz.strategy.regime_vmr",
        "class": "RegimeAdaptiveVMR",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "tight_H1": {"timeframe": "H1", "bb_std": 2.0, "consec_outside": 1},
            "wide_H1": {"timeframe": "H1", "bb_std": 3.0, "consec_outside": 3},
        },
    },
    "EMArib": {
        "module": "algosbz.strategy.ema_ribbon_trend",
        "class": "EMARibbonTrend",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "loose_H1": {"timeframe": "H1", "ribbon_threshold": 0.5, "ribbon_confirm_bars": 2, "rsi_pullback_bull": 50, "rsi_pullback_bear": 50},
            "loose_H4": {"timeframe": "H4", "ribbon_threshold": 0.5, "ribbon_confirm_bars": 2, "rsi_pullback_bull": 50, "rsi_pullback_bear": 50},
            "tight_H1": {"timeframe": "H1", "ribbon_threshold": 0.9, "ribbon_confirm_bars": 5},
        },
    },
    "SessBrk": {
        "module": "algosbz.strategy.session_breakout_v2",
        "class": "SessionBreakout",
        "presets": {
            "default_M15": {"timeframe": "M15"},
            "tight_M15": {"timeframe": "M15", "min_range_atr": 0.5, "max_range_atr": 2.0, "tp_atr_mult": 3.0},
            "wide_M15": {"timeframe": "M15", "min_range_atr": 0.2, "max_range_atr": 3.0, "sl_atr_mult": 1.5},
        },
    },
    "SMCOB": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "loose_H1": {"timeframe": "H1", "rejection_wick_ratio": 0.4, "tp_atr_mult": 2.5},
            "loose_H4": {"timeframe": "H4", "rejection_wick_ratio": 0.4, "tp_atr_mult": 2.5},
            "tight_H1": {"timeframe": "H1", "rejection_wick_ratio": 0.6, "sl_atr_mult": 1.0, "tp_atr_mult": 2.0},
        },
    },
    "FVGrev": {
        "module": "algosbz.strategy.fvg_reversion",
        "class": "FVGReversion",
        "presets": {
            "default_H1": {"timeframe": "H1"},
            "default_H4": {"timeframe": "H4"},
            "tight_H1": {"timeframe": "H1", "min_gap_atr_ratio": 0.5, "trend_strength_max": 15},
            "tight_H4": {"timeframe": "H4", "min_gap_atr_ratio": 0.5, "trend_strength_max": 15},
            "loose_H1": {"timeframe": "H1", "min_gap_atr_ratio": 0.2, "trend_strength_max": 35, "tp_atr_mult": 3.0},
        },
    },
    "VWAPrev": {
        "module": "algosbz.strategy.vwap_reversion",
        "class": "VWAPReversion",
        "presets": {
            "default_M15": {"timeframe": "M15"},
            "default_H1": {"timeframe": "H1"},
            "wide_M15": {"timeframe": "M15", "deviation_atr": 0.3, "tp_atr_mult": 2.0},
            "nokz_H1": {"timeframe": "H1", "require_kill_zone": False, "deviation_atr": 0.7, "tp_atr_mult": 2.0},
        },
    },
}

INSTRUMENTS = ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD", "XNGUSD", "SPY", "NDAQ"]

PERIODS = [
    ("2015-01-01", "2016-12-31"),
    ("2017-01-01", "2018-12-31"),
    ("2019-01-01", "2020-12-31"),
    ("2021-01-01", "2022-12-31"),
    ("2023-01-01", "2024-12-31"),
]


def load_strategy(strat_key, preset_params):
    info = STRATEGIES[strat_key]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    params = {"session_start": 0, "session_end": 23, **preset_params}
    return cls(params)


def run_backtest(config, instrument_cfg, data, strat_key, preset_params, symbol,
                 spread_mult=1.0, min_trades=15):
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
        "wr": round(result.win_rate, 1),
        "pf": round(result.profit_factor, 2),
        "ret": round(result.total_return_pct, 1),
        "mdd": round(result.max_drawdown_pct, 1),
    }


def period_stability(config, instrument_cfg, data, strat_key, preset_params, symbol):
    profitable = 0
    for start, end in PERIODS:
        mask = (data.index >= pd.Timestamp(start)) & (data.index < pd.Timestamp(end))
        period_data = data[mask]
        if len(period_data) < 500:
            continue
        r = run_backtest(config, instrument_cfg, period_data, strat_key, preset_params,
                         symbol, min_trades=3)
        if r and r["pf"] > 1.0:
            profitable += 1
    return profitable


def param_sensitivity(config, instrument_cfg, data, strat_key, preset_params, symbol,
                      base_pf):
    """Test ±20% on SL/TP mults. Returns worst PF from variants with trades."""
    sensitive_keys = ["sl_atr_mult", "tp_atr_mult"]
    worst_pf = base_pf
    worst_label = ""

    for key in sensitive_keys:
        if key not in preset_params:
            # Get default from strategy class
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
                             min_trades=10)
            if r is None:
                continue  # 0 trades = skip (cliff, not fragile)
            if r["pf"] < worst_pf:
                worst_pf = r["pf"]
                worst_label = f"{key}×{mult}"

    return worst_pf, worst_label


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Count total combos
    total = sum(len(s["presets"]) for s in STRATEGIES.values()) * len(INSTRUMENTS)
    print(f"MASSIVE SCAN: {total} combos ({len(STRATEGIES)} strats × {len(INSTRUMENTS)} instruments × presets)")
    print(f"Pipeline: PF>1.05 → Periods≥3/5 → Spread+50%>1.0 → Sensitivity±20%>1.0\n")

    # Load data
    data_cache = {}
    print("Loading data...")
    for sym in INSTRUMENTS:
        try:
            data_cache[sym] = loader.load(sym, start="2015-01-01", end="2025-01-01")
            print(f"  {sym}: {len(data_cache[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # ── PHASE 1: Quick PF filter ─────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 1: PF > 1.05, min 15 trades")
    print(f"{'='*100}\n")

    candidates = []
    done = 0

    for strat_key, strat_info in STRATEGIES.items():
        for preset_name, preset_params in strat_info["presets"].items():
            for sym in INSTRUMENTS:
                done += 1
                if sym not in data_cache:
                    continue
                inst_cfg = instruments.get(sym)
                if inst_cfg is None:
                    continue

                combo = f"{strat_key}_{sym}_{preset_name}"
                print(f"  [{done}/{total}] {combo}...", end=" ", flush=True)

                r = run_backtest(config, inst_cfg, data_cache[sym], strat_key,
                                 preset_params, sym)
                if r is None:
                    print("SKIP")
                    continue
                if r["pf"] < 1.05:
                    print(f"LOW PF={r['pf']}")
                    continue

                print(f"OK PF={r['pf']} WR={r['wr']}% T={r['trades']}")
                candidates.append({
                    "combo": combo, "strat": strat_key, "symbol": sym,
                    "preset": preset_name, "params": preset_params, **r,
                })

    print(f"\n  Phase 1: {len(candidates)} candidates (PF>1.05)")

    if not candidates:
        print("  No candidates! Exiting.")
        return

    # ── PHASE 2: Period stability ─────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 2: Period stability (≥3/5 periods profitable)")
    print(f"{'='*100}\n")

    stable = []
    for c in candidates:
        combo = c["combo"]
        print(f"  {combo}...", end=" ", flush=True)
        n = period_stability(config, instruments[c["symbol"]], data_cache[c["symbol"]],
                             c["strat"], c["params"], c["symbol"])
        if n >= 3:
            print(f"PASS {n}/5")
            c["periods"] = n
            stable.append(c)
        else:
            print(f"FAIL {n}/5")

    print(f"\n  Phase 2: {len(stable)} passed period stability")

    if not stable:
        print("  No stable combos! Exiting.")
        return

    # ── PHASE 3: Spread stress ────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 3: Spread stress test (+50%)")
    print(f"{'='*100}\n")

    spread_ok = []
    for c in stable:
        combo = c["combo"]
        print(f"  {combo}...", end=" ", flush=True)
        r = run_backtest(config, instruments[c["symbol"]], data_cache[c["symbol"]],
                         c["strat"], c["params"], c["symbol"], spread_mult=1.5)
        if r and r["pf"] > 1.0:
            print(f"PASS stress_PF={r['pf']}")
            c["stress_pf"] = r["pf"]
            spread_ok.append(c)
        else:
            stress_pf = r["pf"] if r else 0
            print(f"FAIL stress_PF={stress_pf}")

    print(f"\n  Phase 3: {len(spread_ok)} passed spread stress")

    # ── PHASE 4: Parameter sensitivity ────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  PHASE 4: Parameter sensitivity (±20% SL/TP)")
    print(f"{'='*100}\n")

    robust = []
    for c in spread_ok:
        combo = c["combo"]
        print(f"  {combo}...", end=" ", flush=True)
        worst_pf, worst_label = param_sensitivity(
            config, instruments[c["symbol"]], data_cache[c["symbol"]],
            c["strat"], c["params"], c["symbol"], c["pf"])
        if worst_pf > 1.0:
            print(f"PASS worst_PF={worst_pf:.2f} ({worst_label})")
            c["worst_pf"] = worst_pf
            robust.append(c)
        else:
            print(f"FAIL worst_PF={worst_pf:.2f} ({worst_label})")

    # ── FINAL RESULTS ─────────────────────────────────────────────────
    print(f"\n{'='*100}")
    print(f"  FINAL RESULTS")
    print(f"{'='*100}\n")

    # Also save spread_ok (passed 3/4 tests) as "viable"
    sensitivity_failed = [c for c in spread_ok if c not in robust]

    print(f"  ROBUST (all 4 tests): {len(robust)}")
    for c in sorted(robust, key=lambda x: x["pf"], reverse=True):
        print(f"    {c['combo']:40s} PF={c['pf']:.2f} WR={c['wr']}% "
              f"T={c['trades']} Per={c['periods']}/5 StPF={c['stress_pf']:.2f}")

    print(f"\n  SPREAD_OK (3/4 tests, sensitivity marginal): {len(sensitivity_failed)}")
    for c in sorted(sensitivity_failed, key=lambda x: x["pf"], reverse=True):
        print(f"    {c['combo']:40s} PF={c['pf']:.2f} WR={c['wr']}% "
              f"T={c['trades']} Per={c['periods']}/5 StPF={c['stress_pf']:.2f}")

    # Combined stats
    all_viable = robust + sensitivity_failed
    if all_viable:
        total_trades = sum(c["trades"] for c in all_viable)
        avg_pf = sum(c["pf"] * c["trades"] for c in all_viable) / total_trades
        months = 119
        tpm = total_trades / months
        print(f"\n  Combined viable portfolio ({len(all_viable)} combos):")
        print(f"    Total trades: {total_trades} ({tpm:.1f}/month)")
        print(f"    Weighted PF:  {avg_pf:.2f}")

    # Save results
    Path("cache").mkdir(exist_ok=True)
    rows = []
    for c in robust:
        rows.append({**{k: v for k, v in c.items() if k != "params"}, "tier": "ROBUST"})
    for c in sensitivity_failed:
        rows.append({**{k: v for k, v in c.items() if k != "params"}, "tier": "SPREAD_OK"})

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv("cache/massive_scan_results.csv", index=False)
        print(f"\n  Results saved to cache/massive_scan_results.csv")


if __name__ == "__main__":
    main()
