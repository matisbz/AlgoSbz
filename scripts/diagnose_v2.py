"""
Diagnose v2: test with FTMO-actual DD limits (no safety margin)
and compare conservative vs aggressive DD management.

Usage:
    python -X utf8 scripts/diagnose_v2.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pandas as pd
from copy import deepcopy

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.portfolio_engine import PortfolioEngine, StrategyAllocation
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.WARNING)

ALL_COMBOS = {
    "VMR_USDCHF": {"strategy": "vol_mean_reversion", "symbol": "USDCHF",
                    "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2,
                               "sl_atr_mult": 3.0, "tp_atr_mult": 4.0,
                               "session_start": 0, "session_end": 23}},
    "TPB_XTIUSD": {"strategy": "trend_pullback", "symbol": "XTIUSD",
                    "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                               "sl_atr_mult": 2.0, "tp_atr_mult": 2.5,
                               "session_start": 0, "session_end": 23}},
    "H4MR_XTIUSD": {"strategy": "h4_mean_reversion", "symbol": "XTIUSD",
                     "params": {"bb_std": 2.0, "rsi_oversold": 30, "rsi_overbought": 70,
                                "adx_max": 30, "sl_atr_mult": 1.5, "tp_atr_mult": 2.0}},
    "SwBrk_XTIUSD": {"strategy": "swing_breakout", "symbol": "XTIUSD",
                      "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 15,
                                 "sl_atr_mult": 1.0, "tp_atr_mult": 2.0}},
    "SwBrk_USDJPY": {"strategy": "swing_breakout", "symbol": "USDJPY",
                      "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 20,
                                 "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}},
}

STRATEGY_MAP = {
    "vol_mean_reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "trend_pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
    "h4_mean_reversion": ("algosbz.strategy.h4_mean_reversion", "H4MeanReversion"),
    "swing_breakout": ("algosbz.strategy.swing_breakout", "SwingBreakout"),
}

# Best portfolios from v1
PORTFOLIOS = {
    "Top3+TPB": ["VMR_USDCHF", "TPB_XTIUSD", "H4MR_XTIUSD", "SwBrk_XTIUSD", "SwBrk_USDJPY"],
    "Top3_PF": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD"],
    "Top4_PF": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD", "SwBrk_USDJPY"],
}

RISK_LEVELS = [0.03, 0.04, 0.05]

# DD management modes
DD_MODES = {
    "Conservative": {  # Current: tight margins
        "daily_dd": 0.04, "max_dd": 0.085,
        "eq_tiers": [(0.03, 1.0), (0.05, 0.5), (0.07, 0.25), (0.08, 0.0)],
        "daily_stop": 0.035,
    },
    "FTMO_Actual": {  # Use FTMO actual limits (5%/10%)
        "daily_dd": 0.049, "max_dd": 0.099,
        "eq_tiers": [(0.10, 1.0)],  # No anti-martingale scaling
        "daily_stop": 0.048,
    },
    "Challenge_Aggressive": {  # Push to absolute limits
        "daily_dd": 0.049, "max_dd": 0.099,
        "eq_tiers": [(0.06, 1.0), (0.08, 0.5), (0.095, 0.0)],  # Scale down near limits
        "daily_stop": 0.045,
    },
}


def load_strategy_class(name: str):
    import importlib
    module_path, class_name = STRATEGY_MAP[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def run_test(config, instruments, data_dict, combo_names, risk_pct, dd_mode):
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    cfg.risk.daily_dd_limit = dd_mode["daily_dd"]
    cfg.risk.max_dd_limit = dd_mode["max_dd"]

    eq_cfg = EquityManagerConfig(
        dd_tiers=dd_mode["eq_tiers"],
        daily_stop_threshold=dd_mode["daily_stop"],
        progressive_trades=0,
        consecutive_win_bonus=0,
    )
    engine = PortfolioEngine(cfg, instruments, EquityManager(eq_cfg))

    for name in combo_names:
        entry = ALL_COMBOS[name]
        cls = load_strategy_class(entry["strategy"])
        alloc = StrategyAllocation(
            strategy_class=cls,
            strategy_params=entry.get("params", {}),
            symbols=[entry["symbol"]],
            weight=1.0,
        )
        engine.add_strategy(alloc)

    results = engine.run(data_dict)
    combined = results["combined"]
    equity = combined.equity_curve
    if equity.empty:
        return None

    monthly = equity.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()
    total_months = len(monthly_ret)

    mo_above_8 = int((monthly_ret >= 0.08).sum())
    mo_above_5 = int((monthly_ret >= 0.05).sum())

    # Best/worst month
    best_mo = float(monthly_ret.max() * 100) if len(monthly_ret) > 0 else 0
    worst_mo = float(monthly_ret.min() * 100) if len(monthly_ret) > 0 else 0

    return {
        "trades": combined.total_trades,
        "tpm": round(combined.total_trades / max(1, total_months), 1),
        "wr": round(combined.win_rate, 1),
        "pf": round(combined.profit_factor, 2),
        "ret": round(combined.total_return_pct, 1),
        "mdd": round(combined.max_drawdown_pct, 1),
        "avg_mo": round(float(monthly_ret.mean() * 100), 2),
        "std_mo": round(float(monthly_ret.std() * 100), 2),
        "best_mo": round(best_mo, 1),
        "worst_mo": round(worst_mo, 1),
        "mo8": mo_above_8,
        "mo5": mo_above_5,
        "tot_mo": total_months,
    }


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    symbols = list({v["symbol"] for v in ALL_COMBOS.values()})
    data_dict = {}
    print("Loading data...")
    for sym in symbols:
        data_dict[sym] = loader.load(sym, start="2015-01-01", end="2025-01-01")
        print(f"  {sym}: {len(data_dict[sym]):,} bars")

    print("\n" + "=" * 140)
    print("  IMPACT OF DD LIMITS ON CHALLENGE PERFORMANCE")
    print("  Question: Are our safety margins killing our trade frequency?")
    print("=" * 140)

    rows = []
    total = len(PORTFOLIOS) * len(RISK_LEVELS) * len(DD_MODES)
    done = 0

    for dd_name, dd_mode in DD_MODES.items():
        for port_name, combos in PORTFOLIOS.items():
            for risk in RISK_LEVELS:
                done += 1
                label = f"{dd_name}/{port_name}@{risk*100:.0f}%"
                print(f"  [{done}/{total}] {label}...", end=" ", flush=True)
                r = run_test(config, instruments, data_dict, combos, risk, dd_mode)
                if r:
                    rows.append({
                        "DD_Mode": dd_name,
                        "Portfolio": port_name,
                        "Risk": f"{risk*100:.0f}%",
                        **r,
                    })
                    print(f"Tr/mo={r['tpm']} PF={r['pf']} AvgMo={r['avg_mo']:+.2f}% "
                          f"Mo>8%={r['mo8']}/{r['tot_mo']} Best={r['best_mo']:+.1f}% Worst={r['worst_mo']:+.1f}%")
                else:
                    print("NO DATA")

    print("\n" + "=" * 140)
    print("  FULL RESULTS")
    print("=" * 140)
    df = pd.DataFrame(rows)
    # Show grouped by DD mode
    for dd_name in DD_MODES:
        subset = df[df["DD_Mode"] == dd_name]
        print(f"\n  --- {dd_name} ---")
        print(subset.drop(columns=["DD_Mode"]).to_string(index=False))

    # Key comparison
    print("\n" + "=" * 140)
    print("  KEY COMPARISON: Top3_PF @ 4% across DD modes")
    print("=" * 140)
    for dd_name in DD_MODES:
        match = [r for r in rows if r["DD_Mode"] == dd_name and r["Portfolio"] == "Top3_PF" and r["Risk"] == "4%"]
        if match:
            r = match[0]
            print(f"  {dd_name:25s}: {r['tpm']:5.1f} tr/mo  PF {r['pf']:.2f}  "
                  f"Avg {r['avg_mo']:+5.2f}%/mo  Mo>8%={r['mo8']:2d}/{r['tot_mo']}  "
                  f"MDD {r['mdd']:.1f}%  Best {r['best_mo']:+.1f}%  Worst {r['worst_mo']:+.1f}%")


if __name__ == "__main__":
    main()
