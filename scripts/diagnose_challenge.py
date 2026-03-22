"""
Diagnose what it would take to pass FTMO challenges.
Tests multiple risk levels and portfolio compositions.

Usage:
    python -X utf8 scripts/diagnose_challenge.py
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
from algosbz.risk.prop_firm import PropFirmSimulator

logging.basicConfig(level=logging.WARNING)

ALL_COMBOS = {
    "VMR_USDCHF": {"strategy": "vol_mean_reversion", "symbol": "USDCHF",
                    "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2,
                               "sl_atr_mult": 3.0, "tp_atr_mult": 4.0,
                               "session_start": 0, "session_end": 23}},
    "TPB_GBPJPY": {"strategy": "trend_pullback", "symbol": "GBPJPY",
                    "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                               "sl_atr_mult": 2.0, "tp_atr_mult": 3.0,
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

PORTFOLIOS = {
    "All 6": list(ALL_COMBOS.keys()),
    "No GBPJPY (PF>1.1)": ["VMR_USDCHF", "TPB_XTIUSD", "H4MR_XTIUSD", "SwBrk_XTIUSD", "SwBrk_USDJPY"],
    "High PF (>1.2)": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD", "SwBrk_USDJPY"],
    "Top 3 PF": ["VMR_USDCHF", "H4MR_XTIUSD", "SwBrk_XTIUSD"],
    "Solo H4MR": ["H4MR_XTIUSD"],
}

RISK_LEVELS = [0.02, 0.03, 0.04, 0.05]


def load_strategy_class(name: str):
    import importlib
    module_path, class_name = STRATEGY_MAP[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def run_test(config, instruments, data_dict, combo_names, risk_pct):
    """Run portfolio and return key metrics."""
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = risk_pct
    # Relax internal DD limits for challenge mode (FTMO allows 5%/10%)
    cfg.risk.daily_dd_limit = 0.048   # internal safety: 4.8% (FTMO limit is 5%)
    cfg.risk.max_dd_limit = 0.095     # internal safety: 9.5% (FTMO limit is 10%)

    # No anti-martingale scaling — raw strategy performance
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],  # Full risk until 10% DD
        daily_stop_threshold=0.048,
        progressive_trades=0,
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

    # Monthly returns
    monthly = equity.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()
    total_months = len(monthly_ret)

    mo_above_8 = int((monthly_ret >= 0.08).sum())
    mo_above_5 = int((monthly_ret >= 0.05).sum())

    # Sequential challenge
    simulator = PropFirmSimulator(cfg.prop_firm.phases)
    seq = simulator.sequential_challenge(combined, fee_per_attempt=500, cooldown_days=1)
    p1_passes = sum(1 for a in seq.attempts if a.phase_name == "Fase 1" and a.outcome == "PASS")

    return {
        "trades": combined.total_trades,
        "tpm": round(combined.total_trades / max(1, total_months), 1),
        "wr": round(combined.win_rate, 1),
        "pf": round(combined.profit_factor, 2),
        "ret": round(combined.total_return_pct, 1),
        "mdd": round(combined.max_drawdown_pct, 1),
        "avg_mo": round(float(monthly_ret.mean() * 100), 2),
        "std_mo": round(float(monthly_ret.std() * 100), 2),
        "mo8": mo_above_8,
        "mo5": mo_above_5,
        "tot_mo": total_months,
        "p1": f"{p1_passes}/{seq.total_phase1_attempts}",
        "funded": "Y" if seq.funded else "N",
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

    rows = []
    total = len(PORTFOLIOS) * len(RISK_LEVELS)
    done = 0

    for port_name, combos in PORTFOLIOS.items():
        for risk in RISK_LEVELS:
            done += 1
            print(f"  [{done}/{total}] {port_name} @ {risk*100:.0f}%...", end=" ", flush=True)
            r = run_test(config, instruments, data_dict, combos, risk)
            if r:
                rows.append({"Portfolio": port_name, "Risk": f"{risk*100:.0f}%", **r})
                print(f"PF={r['pf']} Tr/mo={r['tpm']} AvgMo={r['avg_mo']:+.2f}% Mo>8%={r['mo8']}/{r['tot_mo']} P1={r['p1']}")
            else:
                print("NO DATA")

    print("\n" + "=" * 130)
    print("  RESULTS — Can we pass FTMO Phase 1 (+8% in 30 days)?")
    print("=" * 130)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    print("\n  Key: tpm=trades/mo, wr=win%, pf=profit factor, avg_mo=avg monthly return,")
    print("       std_mo=monthly return stdev, mo8/mo5=months above 8%/5%, p1=Phase1 passes/attempts")

    # Find best configs
    if rows:
        best_mo8 = max(rows, key=lambda x: x["mo8"])
        print(f"\n  BEST for months>8%: {best_mo8['Portfolio']} @ {best_mo8['Risk']} "
              f"-> {best_mo8['mo8']}/{best_mo8['tot_mo']} months ({best_mo8['mo8']/best_mo8['tot_mo']*100:.1f}%)")

        best_avg = max(rows, key=lambda x: x["avg_mo"])
        print(f"  BEST avg monthly:   {best_avg['Portfolio']} @ {best_avg['Risk']} "
              f"-> {best_avg['avg_mo']:+.2f}%/month")


if __name__ == "__main__":
    main()
