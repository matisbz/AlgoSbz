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

# Validated strategy-pair combinations (honest backtest, no look-ahead)
# Each combination has been tested across multiple time periods for robustness
PORTFOLIO = [
    # Vol Mean Reversion on USDCHF — STRONGEST EDGE (PF 1.31, profitable 4/5 periods)
    {"strategy": "vol_mean_reversion", "symbol": "USDCHF", "weight": 1.0,
     "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2, "sl_atr_mult": 3.0, "tp_atr_mult": 4.0}},
    # Trend Pullback on GBPJPY — strong trend follower (PF 1.22, profitable 2/3 periods)
    {"strategy": "trend_pullback", "symbol": "GBPJPY", "weight": 0.8,
     "params": {"adx_min": 30, "pullback_zone_atr": 1.5, "sl_atr_mult": 2.5, "tp_atr_mult": 3.5}},
    # Trend Pullback on XTIUSD — commodity trend (PF 1.87 in strong trend periods)
    {"strategy": "trend_pullback", "symbol": "XTIUSD", "weight": 0.6,
     "params": {"adx_min": 30, "pullback_zone_atr": 0.8, "sl_atr_mult": 2.0, "tp_atr_mult": 3.0}},
]

STRATEGY_MAP = {
    "vol_mean_reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "trend_pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
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
