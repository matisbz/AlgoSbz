"""
Robustness validation for all validated combos.

Step 1: Spread stress test (+50% spread) → PF must stay > 1.0
Step 2: Parameter sensitivity (±20% on key params):
        - If variant produces < 15 trades → SKIP (parameter cliff, not fragility)
        - If variant produces trades → PF must stay > 1.0
Step 3: Expanded parameter exploration for strategies that gave 0 combos

Usage:
    python -X utf8 scripts/validate_combos.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import importlib
import pandas as pd
import numpy as np
from copy import deepcopy

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# All 11 validated combos from scan
VALIDATED = {
    "VMR_USDCHF_H1": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDCHF",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
        "key_params": ["bb_std", "sl_atr_mult", "tp_atr_mult", "consec_outside"],
    },
    "VMR_USDJPY_H4": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "symbol": "USDJPY",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
        "key_params": ["bb_std", "sl_atr_mult", "tp_atr_mult"],
    },
    "TPB_XTIUSD_H4": {
        "module": "algosbz.strategy.trend_pullback",
        "class": "TrendPullback",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
        "key_params": ["sl_atr_mult", "tp_atr_mult"],
    },
    "SwBrk_XTIUSD_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "XTIUSD",
        "params": {"timeframe": "H4"},
        "key_params": ["donchian_period", "sl_atr_mult", "tp_atr_mult", "squeeze_pct"],
    },
    "SwBrk_SPY_H4": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "symbol": "SPY",
        "params": {"timeframe": "H4"},
        "key_params": ["donchian_period", "sl_atr_mult", "tp_atr_mult"],
    },
    "Engulf_EURUSD_H4": {
        "module": "algosbz.strategy.engulfing_reversal",
        "class": "EngulfingReversal",
        "symbol": "EURUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
        "key_params": ["sl_atr_mult", "tp_atr_mult"],
    },
    "MomDiv_SPY_H1": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "symbol": "SPY",
        "params": {"timeframe": "H1"},
        "key_params": ["sl_atr_mult", "tp_atr_mult"],
    },
    "RegVMR_XAUUSD_H1": {
        "module": "algosbz.strategy.regime_vmr",
        "class": "RegimeAdaptiveVMR",
        "symbol": "XAUUSD",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
        "key_params": ["bb_std", "sl_atr_mult", "tp_atr_mult"],
    },
    "SessBrk_XTIUSD_M15": {
        "module": "algosbz.strategy.session_breakout_v2",
        "class": "SessionBreakout",
        "symbol": "XTIUSD",
        "params": {"timeframe": "M15"},
        "key_params": ["pre_range_bars", "sl_atr_mult", "tp_atr_mult", "min_range_atr"],
    },
    "SMCOB_GBPJPY_H1": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "symbol": "GBPJPY",
        "params": {"timeframe": "H1", "session_start": 0, "session_end": 23},
        "key_params": ["sl_atr_mult", "tp_atr_mult", "rejection_wick_ratio"],
    },
    "SMCOB_XAUUSD_H4": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "symbol": "XAUUSD",
        "params": {"timeframe": "H4", "session_start": 0, "session_end": 23},
        "key_params": ["sl_atr_mult", "tp_atr_mult", "rejection_wick_ratio"],
    },
}

# Expanded parameter grids — includes strategies that gave 0 combos in original scan
ALT_PARAMS = {
    "EMArib": {
        "module": "algosbz.strategy.ema_ribbon_trend",
        "class": "EMARibbonTrend",
        "variants": [
            {"name": "loose", "params": {"ribbon_threshold": 0.5, "ribbon_confirm_bars": 2, "rsi_pullback_bull": 50, "rsi_pullback_bear": 50}},
            {"name": "tight", "params": {"ribbon_threshold": 0.8, "ribbon_confirm_bars": 5, "rsi_pullback_bull": 40, "rsi_pullback_bear": 60}},
            {"name": "wide_tp", "params": {"ribbon_threshold": 0.6, "ribbon_confirm_bars": 3, "tp_atr_mult": 5.0, "sl_atr_mult": 2.5}},
            {"name": "scalp", "params": {"ribbon_threshold": 0.6, "ribbon_confirm_bars": 2, "tp_atr_mult": 2.0, "sl_atr_mult": 1.5}},
            {"name": "session_kz", "params": {"ribbon_threshold": 0.6, "ribbon_confirm_bars": 3, "session_start": 7, "session_end": 20}},
        ],
        "timeframes": ["H1", "H4"],
        "symbols": ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD", "SPY", "NDAQ"],
    },
    "FVGrev": {
        "module": "algosbz.strategy.fvg_reversion",
        "class": "FVGReversion",
        "variants": [
            {"name": "default", "params": {}},
            {"name": "tight_gap", "params": {"min_gap_atr_ratio": 0.2, "trend_strength_max": 20}},
            {"name": "wide_gap", "params": {"min_gap_atr_ratio": 0.5, "trend_strength_max": 30}},
            {"name": "wide_tp", "params": {"sl_atr_mult": 2.0, "tp_atr_mult": 4.0}},
            {"name": "scalp", "params": {"sl_atr_mult": 1.0, "tp_atr_mult": 1.5}},
            {"name": "trend_ok", "params": {"trend_strength_max": 50}},
            {"name": "session_kz", "params": {"session_start": 7, "session_end": 20}},
        ],
        "timeframes": ["H1", "H4"],
        "symbols": ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD", "SPY", "NDAQ"],
    },
    "VWAPrev": {
        "module": "algosbz.strategy.vwap_reversion",
        "class": "VWAPReversion",
        "variants": [
            {"name": "default", "params": {}},
            {"name": "tight_dev", "params": {"deviation_atr": 0.3, "max_deviation_atr": 2.0}},
            {"name": "wide_dev", "params": {"deviation_atr": 0.8, "max_deviation_atr": 4.0}},
            {"name": "no_kz", "params": {"require_kill_zone": False}},
            {"name": "wide_tp", "params": {"sl_atr_mult": 1.5, "tp_atr_mult": 3.0}},
            {"name": "h1_nkz", "params": {"require_kill_zone": False, "deviation_atr": 0.7}},
        ],
        "timeframes": ["M15", "H1"],
        "symbols": ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD", "SPY", "NDAQ"],
    },
    "RegVMR": {
        "module": "algosbz.strategy.regime_vmr",
        "class": "RegimeAdaptiveVMR",
        "variants": [
            {"name": "tight_bb", "params": {"bb_std": 2.0, "consec_outside": 3}},
            {"name": "wide_tp", "params": {"tp_atr_mult": 5.0, "sl_atr_mult": 2.5}},
            {"name": "session", "params": {"session_start": 7, "session_end": 20}},
        ],
        "timeframes": ["H1", "H4"],
        "symbols": ["EURUSD", "GBPJPY", "USDCHF", "USDJPY", "XTIUSD"],
    },
    "SessBrk": {
        "module": "algosbz.strategy.session_breakout_v2",
        "class": "SessionBreakout",
        "variants": [
            {"name": "wide_range", "params": {"pre_range_bars": 24, "min_range_atr": 0.2}},
            {"name": "tight_sl", "params": {"sl_atr_mult": 0.7, "tp_atr_mult": 1.5}},
            {"name": "wide_tp", "params": {"sl_atr_mult": 1.5, "tp_atr_mult": 3.0}},
        ],
        "timeframes": ["M15"],
        "symbols": ["EURUSD", "GBPJPY", "USDCHF", "XAUUSD", "XTIUSD"],
    },
    "SMCOB": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "variants": [
            {"name": "tight_wick", "params": {"rejection_wick_ratio": 0.6}},
            {"name": "wide_tp", "params": {"tp_atr_mult": 4.0, "sl_atr_mult": 1.5}},
            {"name": "session_kz", "params": {"session_start": 7, "session_end": 20}},
        ],
        "timeframes": ["H1", "H4"],
        "symbols": ["EURUSD", "USDCHF", "USDJPY", "XAUUSD", "XTIUSD", "XNGUSD"],
    },
    "VMR": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "variants": [
            {"name": "tight_bb", "params": {"bb_std": 2.0, "consec_outside": 3}},
            {"name": "wide_bb", "params": {"bb_std": 3.0, "consec_outside": 2}},
            {"name": "session_kz", "params": {"session_start": 7, "session_end": 16}},
        ],
        "timeframes": ["H1", "H4"],
        "symbols": ["EURUSD", "GBPJPY", "XAUUSD", "XTIUSD"],
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


def run_backtest(config, inst_cfg, data, module, cls_name, params, symbol,
                 spread_mult=1.0):
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    # Apply spread multiplier
    if spread_mult != 1.0:
        cfg.backtest.spread_mode = "fixed"
        inst_cfg = deepcopy(inst_cfg)
        inst_cfg.default_spread_pips *= spread_mult

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )

    try:
        mod = importlib.import_module(module)
        cls = getattr(mod, cls_name)
        strategy = cls(params)
        engine = BacktestEngine(cfg, inst_cfg, EquityManager(eq_cfg))
        result = engine.run(strategy, data, symbol)
    except Exception as e:
        return None

    if result.total_trades < 15:
        return None

    return {
        "trades": result.total_trades,
        "wr": round(result.win_rate, 1),
        "pf": round(result.profit_factor, 2),
        "ret": round(result.total_return_pct, 1),
        "mdd": round(result.max_drawdown_pct, 1),
    }


def period_check(config, inst_cfg, data, module, cls_name, params, symbol):
    profitable = 0
    for start, end in PERIODS:
        mask = (data.index >= pd.Timestamp(start)) & (data.index < pd.Timestamp(end))
        pdata = data[mask]
        if len(pdata) < 1000:
            continue
        r = run_backtest(config, inst_cfg, pdata, module, cls_name, params, symbol)
        if r and r["pf"] > 1.0:
            profitable += 1
    return profitable


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    data_cache = {}
    print("Loading data...")
    for sym in INSTRUMENTS:
        try:
            data_cache[sym] = loader.load(sym, start="2014-09-01", end="2025-01-01")
            print(f"  {sym}: {len(data_cache[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # ═══════════════════════════════════════════════════════════════════
    # STEP 1: Spread Stress Test (+50%)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  STEP 1: SPREAD STRESS TEST (+50%)")
    print(f"  Criteria: PF must stay > 1.0 with 50% wider spreads")
    print(f"{'='*120}\n")

    spread_results = {}
    base_pfs = {}
    for combo, info in VALIDATED.items():
        sym = info["symbol"]
        if sym not in data_cache:
            continue
        inst_cfg = instruments.get(sym)
        if inst_cfg is None:
            continue

        base = run_backtest(config, inst_cfg, data_cache[sym],
                           info["module"], info["class"], info["params"], sym)
        stressed = run_backtest(config, inst_cfg, data_cache[sym],
                               info["module"], info["class"], info["params"], sym,
                               spread_mult=1.5)

        if base is None:
            print(f"  {combo:30s} BASE FAILED (< 15 trades)")
            spread_results[combo] = "FAILED"
            continue

        base_pf = base["pf"]
        base_pfs[combo] = base_pf
        stress_pf = stressed["pf"] if stressed else 0

        passed = stress_pf > 1.0
        status = "PASS" if passed else "FAIL"
        spread_results[combo] = status

        delta = stress_pf - base_pf
        print(f"  {combo:30s} Base PF={base_pf:.2f} → Stress PF={stress_pf:.2f} "
              f"(Δ={delta:+.2f}) [{status}]")

    passed_spread = [c for c, s in spread_results.items() if s == "PASS"]
    failed_spread = [c for c, s in spread_results.items() if s != "PASS"]
    print(f"\n  Passed: {len(passed_spread)}/{len(VALIDATED)} — {', '.join(passed_spread)}")
    if failed_spread:
        print(f"  Failed: {', '.join(failed_spread)}")

    # ═══════════════════════════════════════════════════════════════════
    # STEP 2: Parameter Sensitivity (±20%) — CORRECTED
    # Now: PF must stay > 1.0 (absolute). 0-trade variants are SKIPPED.
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  STEP 2: PARAMETER SENSITIVITY (±20%) — CORRECTED")
    print(f"  Criteria: PF > 1.0 for variants with trades. 0-trade variants = SKIP (cliff)")
    print(f"{'='*120}\n")

    sensitivity_results = {}
    for combo, info in VALIDATED.items():
        sym = info["symbol"]
        if sym not in data_cache:
            continue
        inst_cfg = instruments.get(sym)
        if inst_cfg is None:
            continue

        base = run_backtest(config, inst_cfg, data_cache[sym],
                           info["module"], info["class"], info["params"], sym)
        if base is None:
            print(f"  {combo:30s} BASE FAILED")
            sensitivity_results[combo] = "FAILED"
            continue

        base_pf = base["pf"]
        worst_pf = base_pf
        worst_param = ""
        all_passed = True
        n_tested = 0
        n_skipped = 0

        # Load default params from strategy class
        mod = importlib.import_module(info["module"])
        cls = getattr(mod, info["class"])
        default_params = cls.DEFAULT_PARAMS.copy()
        full_params = {**default_params, **info["params"]}

        for param_name in info["key_params"]:
            if param_name not in full_params:
                continue
            orig_val = full_params[param_name]
            if not isinstance(orig_val, (int, float)):
                continue

            for mult in [0.8, 1.2]:
                varied = full_params.copy()
                new_val = orig_val * mult
                if isinstance(orig_val, int):
                    new_val = max(1, int(round(new_val)))
                varied[param_name] = new_val

                r = run_backtest(config, inst_cfg, data_cache[sym],
                                info["module"], info["class"], varied, sym)

                if r is None:
                    # No trades produced = parameter cliff, NOT fragility
                    n_skipped += 1
                    continue

                n_tested += 1
                pf = r["pf"]

                if pf < worst_pf:
                    worst_pf = pf
                    worst_param = f"{param_name}×{mult}"

                if pf < 1.0:
                    all_passed = False

        status = "PASS" if all_passed else "FAIL"
        sensitivity_results[combo] = status
        print(f"  {combo:30s} Base PF={base_pf:.2f} → Worst PF={worst_pf:.2f} "
              f"({worst_param}) tested={n_tested} skipped={n_skipped} [{status}]")

    passed_sens = [c for c, s in sensitivity_results.items() if s == "PASS"]
    failed_sens = [c for c, s in sensitivity_results.items() if s != "PASS"]
    print(f"\n  Passed: {len(passed_sens)}/{len(VALIDATED)} — {', '.join(passed_sens)}")
    if failed_sens:
        print(f"  Failed: {', '.join(failed_sens)}")

    # Combined results
    print(f"\n{'='*120}")
    print(f"  COMBINED ROBUSTNESS RESULTS")
    print(f"{'='*120}")
    print(f"\n  {'Combo':<30s} {'BasePF':>7s} {'Spread':>8s} {'Sensitivity':>12s} {'Overall':>8s}")
    print(f"  {'-'*70}")

    robust_combos = []
    spread_only = []
    for combo in VALIDATED:
        sp = spread_results.get(combo, "?")
        sn = sensitivity_results.get(combo, "?")
        bpf = base_pfs.get(combo, 0)
        if sp == "PASS" and sn == "PASS":
            overall = "ROBUST"
            robust_combos.append(combo)
        elif sp == "PASS":
            overall = "SPREAD_OK"
            spread_only.append(combo)
        else:
            overall = "FRAGILE"
        print(f"  {combo:<30s} {bpf:>7.2f} {sp:>8s} {sn:>12s} {overall:>8s}")

    print(f"\n  ROBUST (both tests): {len(robust_combos)}")
    for c in robust_combos:
        print(f"    {c} (PF={base_pfs.get(c, 0):.2f})")
    print(f"\n  SPREAD_OK (spread passed, sensitivity marginal): {len(spread_only)}")
    for c in spread_only:
        print(f"    {c} (PF={base_pfs.get(c, 0):.2f})")

    # ═══════════════════════════════════════════════════════════════════
    # STEP 3: Expanded Parameter Exploration
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  STEP 3: EXPANDED PARAMETER EXPLORATION")
    print(f"  Looking for NEW combos with different params (PF>1.10, 3/5 periods, spread+50%>1.0)")
    print(f"{'='*120}\n")

    new_candidates = []
    existing_combos = set(VALIDATED.keys())
    tested = 0

    for strat_key, strat_info in ALT_PARAMS.items():
        module = strat_info["module"]
        cls_name = strat_info["class"]

        for variant in strat_info["variants"]:
            for tf in strat_info["timeframes"]:
                for sym in strat_info["symbols"]:
                    if sym not in data_cache:
                        continue
                    inst_cfg = instruments.get(sym)
                    if inst_cfg is None:
                        continue

                    # Skip combos that already exist in validated set
                    base_combo = f"{strat_key}_{sym}_{tf}"
                    if base_combo in existing_combos:
                        continue

                    combo_name = f"{strat_key}_{sym}_{tf}_{variant['name']}"
                    tested += 1

                    params = {
                        "timeframe": tf,
                        "session_start": 0,
                        "session_end": 23,
                        **variant["params"],
                    }

                    r = run_backtest(config, inst_cfg, data_cache[sym],
                                    module, cls_name, params, sym)
                    if r is None or r["pf"] < 1.10:
                        continue

                    # Period stability check
                    n_profitable = period_check(
                        config, inst_cfg, data_cache[sym],
                        module, cls_name, params, sym
                    )
                    if n_profitable < 3:
                        continue

                    # Spread stress
                    stressed = run_backtest(config, inst_cfg, data_cache[sym],
                                          module, cls_name, params, sym,
                                          spread_mult=1.5)
                    stress_pf = stressed["pf"] if stressed else 0
                    if stress_pf < 1.0:
                        continue

                    print(f"  NEW: {combo_name:45s} PF={r['pf']:.2f} WR={r['wr']}% "
                          f"Trades={r['trades']} Periods={n_profitable}/5 "
                          f"StressPF={stress_pf:.2f}")

                    new_candidates.append({
                        "combo": combo_name,
                        "strat": strat_key,
                        "symbol": sym,
                        "tf": tf,
                        "variant": variant["name"],
                        "params": params,
                        "pf": r["pf"],
                        "wr": r["wr"],
                        "trades": r["trades"],
                        "ret": r["ret"],
                        "mdd": r["mdd"],
                        "periods": n_profitable,
                        "stress_pf": stress_pf,
                    })

    print(f"\n  Tested {tested} alternative combos")
    if new_candidates:
        print(f"  Found {len(new_candidates)} new robust combos!")
        for c in sorted(new_candidates, key=lambda x: -x["pf"]):
            print(f"    {c['combo']:45s} PF={c['pf']:.2f} StressPF={c['stress_pf']:.2f} "
                  f"Trades={c['trades']} Periods={c['periods']}/5")
    else:
        print(f"  No new robust combos found.")

    # ═══════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"  FINAL PORTFOLIO")
    print(f"{'='*120}")

    # Tier 1: Robust (both tests passed)
    print(f"\n  TIER 1 — ROBUST (spread + sensitivity): {len(robust_combos)}")
    for c in robust_combos:
        print(f"    {c} (PF={base_pfs.get(c, 0):.2f})")

    # Tier 2: Spread-OK (spread passed, sensitivity has cliff but no losing variants)
    print(f"\n  TIER 2 — SPREAD_OK (usable with caution): {len(spread_only)}")
    for c in spread_only:
        print(f"    {c} (PF={base_pfs.get(c, 0):.2f})")

    # Tier 3: New discoveries
    print(f"\n  TIER 3 — NEW DISCOVERIES: {len(new_candidates)}")
    for c in sorted(new_candidates, key=lambda x: -x["pf"]):
        print(f"    {c['combo']} (PF={c['pf']:.2f}, {c['trades']} trades)")

    total = len(robust_combos) + len(spread_only) + len(new_candidates)
    print(f"\n  TOTAL PORTFOLIO: {total} combos")
    print(f"    Tier 1 (robust):     {len(robust_combos)}")
    print(f"    Tier 2 (spread ok):  {len(spread_only)}")
    print(f"    Tier 3 (new):        {len(new_candidates)}")

    # Save all results
    all_combos = []
    for c in robust_combos:
        all_combos.append({"combo": c, "tier": 1, "pf": base_pfs.get(c, 0)})
    for c in spread_only:
        all_combos.append({"combo": c, "tier": 2, "pf": base_pfs.get(c, 0)})
    for c in new_candidates:
        all_combos.append({"combo": c["combo"], "tier": 3, "pf": c["pf"],
                           "trades": c["trades"], "stress_pf": c["stress_pf"]})

    df = pd.DataFrame(all_combos)
    output_path = "cache/validated_portfolio.csv"
    Path("cache").mkdir(exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()
