"""
Parameter sweep and walk-forward optimization.

Uso:
    python scripts/optimize.py --symbol USDCHF --strategy vol_mean_reversion --start 2015-01-01 --end 2024-12-31
"""
import argparse
import itertools
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algosbz.core.config import load_config, load_instrument_config
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.optimization.objective import prop_firm_objective

STRATEGIES = {
    "vol_mean_reversion": ("algosbz.strategy.volatility_mean_reversion", "VolatilityMeanReversion"),
    "trend_pullback": ("algosbz.strategy.trend_pullback", "TrendPullback"),
}

# Parameter grids for each strategy
PARAM_GRIDS = {
    "vol_mean_reversion": {
        "bb_period": [15, 20, 25],
        "bb_std": [2.0, 2.5, 3.0],
        "consec_outside": [1, 2, 3],
        "adx_max": [25, 30, 40],
        "sl_atr_mult": [2.0, 2.5, 3.0],
        "tp_atr_mult": [3.0, 3.5, 4.0],
    },
    "trend_pullback": {
        "fast_ema": [13, 21, 34],
        "slow_ema": [50, 100],
        "adx_min": [20, 25, 30],
        "pullback_zone_atr": [0.5, 0.8, 1.0, 1.5],
        "sl_atr_mult": [2.0, 2.5, 3.0],
        "tp_atr_mult": [3.0, 3.5, 4.5],
    },
}


def load_strategy(name: str, params: dict):
    module_path, class_name = STRATEGIES[name]
    import importlib
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(params)


def param_combinations(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def main():
    parser = argparse.ArgumentParser(description="AlgoSbz Optimizer")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--strategy", required=True, choices=list(STRATEGIES.keys()))
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")

    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    config = load_config()
    instrument = load_instrument_config(args.symbol)
    loader = DataLoader()
    data = loader.load(args.symbol, start=args.start, end=args.end)

    print(f"Loaded {len(data):,} bars for {args.symbol}")

    grid = PARAM_GRIDS.get(args.strategy, {})
    combos = param_combinations(grid)
    print(f"Testing {len(combos)} parameter combinations...")

    results = []

    for i, params in enumerate(combos):
        strategy = load_strategy(args.strategy, params)
        engine = BacktestEngine(config, instrument)
        result = engine.run(strategy, data, args.symbol)

        pfm_score = prop_firm_objective(result)
        entry = {
            **params,
            "trades": result.total_trades,
            "win_rate": round(result.win_rate, 1),
            "pf": round(result.profit_factor, 2),
            "sharpe": round(result.sharpe_ratio, 2),
            "max_dd": round(result.max_drawdown_pct, 2),
            "return_pct": round(result.total_return_pct, 2),
            "pfm_score": round(pfm_score, 2),
        }
        results.append(entry)

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(combos)} completed...")

    df = pd.DataFrame(results)

    # Sort by prop firm objective score, filter min trades
    df = df[df["trades"] >= 10]
    df = df.sort_values("pfm_score", ascending=False)

    print(f"\n{'='*80}")
    print(f"  TOP {args.top} RESULTS (sorted by Prop Firm Score, min 10 trades)")
    print(f"{'='*80}")
    print(df.head(args.top).to_string(index=False))

    # Save full results
    output_path = f"cache/optimize_{args.strategy}_{args.symbol}.csv"
    df.to_csv(output_path, index=False)
    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
