"""
Portfolio backtest: run best strategies across multiple pairs.

Usage:
    python scripts/run_portfolio.py --start 2015-01-01 --end 2025-01-01
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algosbz.core.config import load_config, load_instrument_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.portfolio_engine import PortfolioEngine, StrategyAllocation
from algosbz.risk.prop_firm import PropFirmSimulator
from algosbz.risk.equity_manager import EquityManager

# FTMO-optimized portfolio: 94% funding rate at 2% risk, $3.3K avg cost per funded account
# Validated across 10 years (2015-2024), 16/17 sequential challenges funded
# Session hours extended to 24h for maximum frequency (14.4 trades/month combined)
PORTFOLIO = [
    # ── H1 Strategies ──────────────────────────────────────────────
    # Vol Mean Reversion on USDCHF — core edge (PF 1.27 @ 2%, 3.3 trades/mo)
    {"strategy": "vol_mean_reversion", "symbol": "USDCHF", "weight": 1.0,
     "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2,
                "sl_atr_mult": 3.0, "tp_atr_mult": 4.0,
                "session_start": 0, "session_end": 23}},
    # Trend Pullback on GBPJPY — frequency driver (PF 1.04, 7.7 trades/mo)
    {"strategy": "trend_pullback", "symbol": "GBPJPY", "weight": 1.0,
     "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                "sl_atr_mult": 2.0, "tp_atr_mult": 3.0,
                "session_start": 0, "session_end": 23}},
    # Trend Pullback on XTIUSD — commodity diversifier (PF 1.10, 3.4 trades/mo)
    {"strategy": "trend_pullback", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                "sl_atr_mult": 2.0, "tp_atr_mult": 2.5,
                "session_start": 0, "session_end": 23}},
    # ── H4 Strategies ──────────────────────────────────────────────
    # H4 Mean Reversion on XTIUSD — highest PF (PF 1.67, 4/5 periods profitable)
    {"strategy": "h4_mean_reversion", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"bb_std": 2.0, "rsi_oversold": 30, "rsi_overbought": 70,
                "adx_max": 30, "sl_atr_mult": 1.5, "tp_atr_mult": 2.0}},
    # Swing Breakout on XTIUSD — volatility expansion (PF 1.41, 3/5 periods)
    {"strategy": "swing_breakout", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 15,
                "sl_atr_mult": 1.0, "tp_atr_mult": 2.0}},
    # Swing Breakout on USDJPY — yen diversifier (PF 1.28, 3/5 periods)
    {"strategy": "swing_breakout", "symbol": "USDJPY", "weight": 1.0,
     "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 20,
                "sl_atr_mult": 1.5, "tp_atr_mult": 3.0}},
]

STRATEGY_MAP = {
    "vol_mean_reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "trend_pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
    "h4_mean_reversion": ("algosbz.strategy.h4_mean_reversion", "H4MeanReversion"),
    "swing_breakout": ("algosbz.strategy.swing_breakout", "SwingBreakout"),
}


def load_strategy_class(name: str):
    import importlib
    module_path, class_name = STRATEGY_MAP[name]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def main():
    parser = argparse.ArgumentParser(description="AlgoSbz Portfolio Backtest")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-01-01")
    parser.add_argument("--monte-carlo", type=int, default=1000)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Collect unique symbols
    symbols = list({p["symbol"] for p in PORTFOLIO})
    data_dict = {}
    for sym in symbols:
        try:
            data_dict[sym] = loader.load(sym, start=args.start, end=args.end)
            print(f"  {sym}: {len(data_dict[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED — {e}")

    # Build allocations — one per entry (each has its own params)
    engine = PortfolioEngine(config, instruments, EquityManager())

    for entry in PORTFOLIO:
        cls = load_strategy_class(entry["strategy"])
        params = entry.get("params", {})
        alloc = StrategyAllocation(
            strategy_class=cls,
            strategy_params=params,
            symbols=[entry["symbol"]],
            weight=entry["weight"],
        )
        engine.add_strategy(alloc)

    print(f"\nRunning portfolio with {len(PORTFOLIO)} strategy-pair combinations...")
    results = engine.run(data_dict)

    # Print individual results
    print("\n" + "=" * 80)
    print("  INDIVIDUAL STRATEGY RESULTS")
    print("=" * 80)

    for name, result in results["individual"].items():
        metrics = result.metrics_summary()
        print(f"\n  {name}:")
        for k, v in metrics.items():
            print(f"    {k:<20s}: {v}")

    # Combined results
    combined = results["combined"]
    print("\n" + "=" * 80)
    print("  COMBINED PORTFOLIO")
    print("=" * 80)
    metrics = combined.metrics_summary()
    for k, v in metrics.items():
        print(f"  {k:<20s}: {v}")

    # Correlation matrix
    corr = results["correlation_matrix"]
    if not corr.empty:
        print("\n" + "-" * 80)
        print("  CORRELATION MATRIX (daily returns)")
        print("-" * 80)
        print(corr.round(2).to_string())

    # Monte Carlo on combined
    if args.monte_carlo > 0 and config.prop_firm:
        print("\n" + "-" * 80)
        print(f"  MONTE CARLO — COMBINED PORTFOLIO ({args.monte_carlo} sims)")
        print("-" * 80)

        simulator = PropFirmSimulator(config.prop_firm.phases)
        for phase in config.prop_firm.phases:
            mc = simulator.monte_carlo(combined, phase, args.monte_carlo)
            print(f"\n  {phase.name}:")
            print(f"    Pass Rate:      {mc['pass_rate']:.1f}%")
            print(f"    Avg Profit:     {mc['avg_profit']:.2f}%")
            print(f"    Median Profit:  {mc['median_profit']:.2f}%")
            print(f"    Avg Max DD:     {mc['avg_max_dd']:.2f}%")
            print(f"    P5-P95 Profit:  {mc['p5_profit']:.2f}% to {mc['p95_profit']:.2f}%")
            if "fail_breakdown" in mc:
                print(f"    Fail breakdown: {mc['fail_breakdown']}")


if __name__ == "__main__":
    main()
