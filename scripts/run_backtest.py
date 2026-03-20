"""
CLI para ejecutar backtests.

Uso:
    python scripts/run_backtest.py --symbol USDCHF --strategy vol_mean_reversion --start 2015-01-01 --end 2024-12-31 --challenge
    python scripts/run_backtest.py --symbol GBPJPY --strategy trend_pullback --monte-carlo 2000
"""
import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algosbz.core.config import load_config, load_instrument_config
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.prop_firm import PropFirmSimulator

STRATEGIES = {
    "vol_mean_reversion": "algosbz.strategy.volatility_mean_reversion.VolatilityMeanReversion",
    "trend_pullback": "algosbz.strategy.trend_pullback.TrendPullback",
}


def load_strategy(name: str, params: dict = None):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGIES.keys())}")

    module_path, class_name = STRATEGIES[name].rsplit(".", 1)
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(params)


def main():
    parser = argparse.ArgumentParser(description="AlgoSbz Backtester")
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g., EURUSD)")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--challenge", action="store_true", help="Run prop firm challenge evaluation")
    parser.add_argument("--monte-carlo", type=int, default=0, help="Number of Monte Carlo simulations")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = load_config()
    instrument = load_instrument_config(args.symbol)

    # Load data
    loader = DataLoader()
    data = loader.load(args.symbol, start=args.start, end=args.end)
    print(f"\nLoaded {len(data):,} bars for {args.symbol} ({data.index[0]} to {data.index[-1]})")

    # Create and run strategy
    strategy = load_strategy(args.strategy)
    engine = BacktestEngine(config, instrument)
    result = engine.run(strategy, data, args.symbol)

    # Print results
    print("\n" + "=" * 60)
    print(f"  {strategy.name} on {args.symbol}")
    print("=" * 60)

    metrics = result.metrics_summary()
    for key, value in metrics.items():
        print(f"  {key:<20s}: {value}")

    # Prop firm challenge evaluation
    if args.challenge and config.prop_firm:
        print("\n" + "-" * 60)
        print("  PROP FIRM CHALLENGE EVALUATION")
        print("-" * 60)

        simulator = PropFirmSimulator(config.prop_firm.phases)
        challenge_result = simulator.evaluate(result)
        print(challenge_result.summary)

    # Monte Carlo
    if args.monte_carlo > 0 and config.prop_firm:
        print("\n" + "-" * 60)
        print(f"  MONTE CARLO ({args.monte_carlo} simulations)")
        print("-" * 60)

        simulator = PropFirmSimulator(config.prop_firm.phases)
        for phase in config.prop_firm.phases:
            mc = simulator.monte_carlo(result, phase, args.monte_carlo)
            print(f"\n  {phase.name}:")
            print(f"    Pass Rate:      {mc['pass_rate']:.1f}%")
            print(f"    Avg Profit:     {mc['avg_profit']:.2f}%")
            print(f"    Median Profit:  {mc['median_profit']:.2f}%")
            print(f"    Avg Max DD:     {mc['avg_max_dd']:.2f}%")
            print(f"    P5-P95 Profit:  {mc['p5_profit']:.2f}% to {mc['p95_profit']:.2f}%")


if __name__ == "__main__":
    main()
