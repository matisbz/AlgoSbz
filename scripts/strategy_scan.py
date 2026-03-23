"""
Massive strategy scan: test ALL strategy × instrument × timeframe combinations.

Goal: Find every viable combo for the super-deck portfolio.
Viable = PF > 1.10, min 20 trades, profitable in >= 3/5 two-year periods.

Usage:
    python -X utf8 scripts/strategy_scan.py
    python -X utf8 scripts/strategy_scan.py --min-pf 1.15
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import logging
import pandas as pd
from copy import deepcopy

from algosbz.core.config import load_config, load_instrument_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

logging.basicConfig(level=logging.ERROR)

# All strategies with their default + alternate timeframe params
STRATEGIES = {
    "VMR": {
        "module": "algosbz.strategy.volatility_mean_reversion",
        "class": "VolatilityMeanReversion",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "TPB": {
        "module": "algosbz.strategy.trend_pullback",
        "class": "TrendPullback",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "SwBrk": {
        "module": "algosbz.strategy.swing_breakout",
        "class": "SwingBreakout",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "IBB": {
        "module": "algosbz.strategy.inside_bar_breakout",
        "class": "InsideBarBreakout",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "Engulf": {
        "module": "algosbz.strategy.engulfing_reversal",
        "class": "EngulfingReversal",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "StrBrk": {
        "module": "algosbz.strategy.structure_break",
        "class": "StructureBreak",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "MomDiv": {
        "module": "algosbz.strategy.momentum_divergence",
        "class": "MomentumDivergence",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "RegVMR": {
        "module": "algosbz.strategy.regime_vmr",
        "class": "RegimeAdaptiveVMR",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "EMArib": {
        "module": "algosbz.strategy.ema_ribbon_trend",
        "class": "EMARibbonTrend",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "SessBrk": {
        "module": "algosbz.strategy.session_breakout_v2",
        "class": "SessionBreakout",
        "timeframes": {
            "M15": {"timeframe": "M15"},
        },
    },
    "SMCOB": {
        "module": "algosbz.strategy.smc_order_block",
        "class": "SMCOrderBlock",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "FVGrev": {
        "module": "algosbz.strategy.fvg_reversion",
        "class": "FVGReversion",
        "timeframes": {
            "H1": {"timeframe": "H1"},
            "H4": {"timeframe": "H4"},
        },
    },
    "VWAPrev": {
        "module": "algosbz.strategy.vwap_reversion",
        "class": "VWAPReversion",
        "timeframes": {
            "M15": {"timeframe": "M15"},
            "H1": {"timeframe": "H1"},
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


def load_strategy(strat_key, tf_params):
    import importlib
    info = STRATEGIES[strat_key]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    params = {"session_start": 0, "session_end": 23, **tf_params}
    return cls(params)


def test_combo(config, instrument_cfg, data, strat_key, tf_key, tf_params, symbol,
               min_trades=20):
    """Run single combo over full period. Return metrics or None."""
    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.049
    cfg.risk.max_dd_limit = 0.099

    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.10, 1.0)],
        daily_stop_threshold=0.048,
        progressive_trades=0,
        consecutive_win_bonus=0,
    )

    try:
        strategy = load_strategy(strat_key, tf_params)
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
        "sharpe": round(result.sharpe_ratio, 2),
        "result": result,
    }


def period_stability(config, instrument_cfg, data, strat_key, tf_params, symbol):
    """Test if combo is profitable in >= 3/5 two-year periods."""
    profitable_periods = 0
    period_results = []

    for start, end in PERIODS:
        mask = (data.index >= pd.Timestamp(start)) & (data.index < pd.Timestamp(end))
        period_data = data[mask]
        if len(period_data) < 1000:
            period_results.append("skip")
            continue

        r = test_combo(config, instrument_cfg, period_data, strat_key, "", tf_params, symbol,
                       min_trades=5)  # Lower threshold for per-period validation
        if r is None:
            period_results.append("low_n")
            continue

        if r["pf"] > 1.0:
            profitable_periods += 1
            period_results.append(f"PF{r['pf']}")
        else:
            period_results.append(f"PF{r['pf']}")

    return profitable_periods, period_results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-pf", type=float, default=1.10)
    parser.add_argument("--min-trades", type=int, default=20)
    args = parser.parse_args()

    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Load all data
    data_cache = {}
    print("Loading all instrument data...")
    for sym in INSTRUMENTS:
        try:
            data_cache[sym] = loader.load(sym, start="2015-01-01", end="2025-01-01")
            print(f"  {sym}: {len(data_cache[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    total_combos = len(STRATEGIES) * len(INSTRUMENTS) * 2  # 2 timeframes
    print(f"\nScanning {total_combos} combinations (4 strategies x {len(INSTRUMENTS)} instruments x 2 TF)...")
    print(f"Filters: PF >= {args.min_pf}, min {args.min_trades} trades\n")

    # Phase 1: Quick scan for overall PF
    viable = []
    rejected = []
    done = 0

    for strat_key in STRATEGIES:
        for sym in INSTRUMENTS:
            if sym not in data_cache:
                done += 2
                continue
            data = data_cache[sym]
            inst_cfg = instruments.get(sym)
            if inst_cfg is None:
                done += 2
                continue

            for tf_key, tf_params in STRATEGIES[strat_key]["timeframes"].items():
                done += 1
                combo_name = f"{strat_key}_{sym}_{tf_key}"
                print(f"  [{done}/{total_combos}] {combo_name}...", end=" ", flush=True)

                r = test_combo(config, inst_cfg, data, strat_key, tf_key, tf_params, sym)

                if r is None:
                    print("SKIP (< 20 trades)")
                    rejected.append({"combo": combo_name, "reason": "low_trades"})
                    continue

                if r["pf"] < args.min_pf:
                    print(f"REJECT PF={r['pf']} ({r['trades']} trades)")
                    rejected.append({"combo": combo_name, "reason": f"pf={r['pf']}", "trades": r["trades"]})
                    continue

                print(f"CANDIDATE PF={r['pf']} WR={r['wr']}% ({r['trades']} trades, MDD={r['mdd']}%)")
                viable.append({
                    "combo": combo_name,
                    "strat": strat_key,
                    "symbol": sym,
                    "tf": tf_key,
                    "tf_params": tf_params,
                    **{k: v for k, v in r.items() if k != "result"},
                })

    print(f"\n{'='*100}")
    print(f"  PHASE 1 RESULTS: {len(viable)} candidates passed PF >= {args.min_pf}")
    print(f"{'='*100}")

    if not viable:
        print("  No viable combos found!")
        return

    for v in sorted(viable, key=lambda x: x["pf"], reverse=True):
        print(f"  {v['combo']:25s} PF={v['pf']:.2f} WR={v['wr']}% "
              f"Trades={v['trades']} Ret={v['ret']:+.1f}% MDD={v['mdd']:.1f}%")

    # Phase 2: Period stability validation
    print(f"\n{'='*100}")
    print(f"  PHASE 2: Period stability validation (must be profitable in >= 3/5 periods)")
    print(f"{'='*100}")

    validated = []
    for v in viable:
        combo_name = v["combo"]
        sym = v["symbol"]
        strat_key = v["strat"]
        tf_params = v["tf_params"]
        inst_cfg = instruments.get(sym)

        print(f"  {combo_name}...", end=" ", flush=True)
        n_profitable, period_details = period_stability(
            config, inst_cfg, data_cache[sym], strat_key, tf_params, sym
        )

        if n_profitable >= 3:
            print(f"VALIDATED {n_profitable}/5 [{', '.join(period_details)}]")
            v["periods_profitable"] = n_profitable
            v["period_details"] = period_details
            validated.append(v)
        else:
            print(f"REJECTED {n_profitable}/5 [{', '.join(period_details)}]")

    # Final results
    print(f"\n{'='*100}")
    print(f"  FINAL SUPER-DECK: {len(validated)} validated combos")
    print(f"{'='*100}")

    if not validated:
        print("  No combos passed both filters!")
        return

    for v in sorted(validated, key=lambda x: x["pf"], reverse=True):
        print(f"  {v['combo']:25s} PF={v['pf']:.2f} WR={v['wr']}% "
              f"Trades={v['trades']} Periods={v['periods_profitable']}/5 "
              f"Ret={v['ret']:+.1f}% MDD={v['mdd']:.1f}%")

    # Estimate combined portfolio
    total_trades = sum(v["trades"] for v in validated)
    avg_pf = sum(v["pf"] * v["trades"] for v in validated) / total_trades
    months = 119
    trades_per_month = total_trades / months

    print(f"\n  Combined estimates:")
    print(f"    Total combos:       {len(validated)}")
    print(f"    Total trades:       {total_trades} ({trades_per_month:.1f}/month)")
    print(f"    Weighted avg PF:    {avg_pf:.2f}")
    print(f"    Expected monthly:   ~{trades_per_month * 0.02 * (avg_pf - 1) / (avg_pf + 1) * 100:.1f}% at 2% risk")

    # Save results
    df = pd.DataFrame([{
        "combo": v["combo"], "strat": v["strat"], "symbol": v["symbol"],
        "tf": v["tf"], "pf": v["pf"], "wr": v["wr"], "trades": v["trades"],
        "ret": v["ret"], "mdd": v["mdd"], "periods": v["periods_profitable"],
    } for v in validated])
    output_path = "cache/strategy_scan_results.csv"
    Path("cache").mkdir(exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"\n  Results saved to {output_path}")


if __name__ == "__main__":
    main()
