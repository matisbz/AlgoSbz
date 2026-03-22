"""
Compare static risk vs dynamic challenge risk on FTMO sequential simulation.

Runs the full portfolio with both approaches and compares:
1. Pass rates (Phase 1, Phase 2, overall funding)
2. Time to funded
3. Fee costs
4. Risk-adjusted metrics

Usage:
    python -X utf8 scripts/challenge_comparison.py
    python -X utf8 scripts/challenge_comparison.py --start 2015-01-01 --end 2025-01-01
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algosbz.core.config import load_config, load_all_instruments
from algosbz.data.loader import DataLoader
from algosbz.backtest.portfolio_engine import PortfolioEngine, StrategyAllocation
from algosbz.risk.prop_firm import PropFirmSimulator
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig
from algosbz.risk.challenge_risk import ChallengeEquityManager, ChallengeRiskConfig

# Portfolio definition (same as run_portfolio.py)
PORTFOLIO = [
    {"strategy": "vol_mean_reversion", "symbol": "USDCHF", "weight": 1.0,
     "params": {"bb_std": 2.5, "adx_max": 30, "consec_outside": 2,
                "sl_atr_mult": 3.0, "tp_atr_mult": 4.0,
                "session_start": 0, "session_end": 23}},
    {"strategy": "trend_pullback", "symbol": "GBPJPY", "weight": 1.0,
     "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                "sl_atr_mult": 2.0, "tp_atr_mult": 3.0,
                "session_start": 0, "session_end": 23}},
    {"strategy": "trend_pullback", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"adx_min": 20, "pullback_zone_atr": 1.5,
                "sl_atr_mult": 2.0, "tp_atr_mult": 2.5,
                "session_start": 0, "session_end": 23}},
    {"strategy": "h4_mean_reversion", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"bb_std": 2.0, "rsi_oversold": 30, "rsi_overbought": 70,
                "adx_max": 30, "sl_atr_mult": 1.5, "tp_atr_mult": 2.0}},
    {"strategy": "swing_breakout", "symbol": "XTIUSD", "weight": 1.0,
     "params": {"donchian_period": 20, "squeeze_pct": 0.8, "adx_min": 15,
                "sl_atr_mult": 1.0, "tp_atr_mult": 2.0}},
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


def run_portfolio_with_manager(config, instruments, data_dict, equity_manager):
    """Run the full portfolio with a specific equity manager."""
    engine = PortfolioEngine(config, instruments, equity_manager)

    for entry in PORTFOLIO:
        cls = load_strategy_class(entry["strategy"])
        alloc = StrategyAllocation(
            strategy_class=cls,
            strategy_params=entry.get("params", {}),
            symbols=[entry["symbol"]],
            weight=entry["weight"],
        )
        engine.add_strategy(alloc)

    return engine.run(data_dict)


def print_sequential_results(label, seq_result):
    """Print sequential challenge results."""
    print(f"\n  {'='*60}")
    print(f"  {label}")
    print(f"  {'='*60}")
    print(f"  Funded:             {'YES' if seq_result.funded else 'NO'}")
    print(f"  Phase 1 attempts:   {seq_result.total_phase1_attempts} (pass rate: {seq_result.phase1_pass_rate:.0f}%)")
    print(f"  Phase 2 attempts:   {seq_result.total_phase2_attempts} (pass rate: {seq_result.phase2_pass_rate:.0f}%)")
    print(f"  Total fees:         ${seq_result.total_fees:,.0f}")
    if seq_result.funded and seq_result.total_calendar_days:
        print(f"  Days to funded:     {seq_result.total_calendar_days}")
    if seq_result.funded and seq_result.total_fees > 0:
        print(f"  Cost per funded:    ${seq_result.total_fees:,.0f}")

    # Count funded accounts over the full period
    funded_count = 0
    for a in seq_result.attempts:
        if a.phase_name == "Fase 2" and a.outcome == "PASS":
            funded_count += 1

    # Attempt details
    print(f"\n  Attempt log:")
    for a in seq_result.attempts:
        marker = "+" if a.outcome == "PASS" else "X"
        print(f"    [{marker}] {a.phase_name} #{a.attempt_number}: "
              f"{a.start_date} to {a.end_date} — {a.outcome} "
              f"({a.profit_pct:+.1f}%, DD {a.max_overall_dd_pct:.1f}%, "
              f"{a.trades_in_attempt} trades)")


def main():
    parser = argparse.ArgumentParser(description="Challenge Risk Comparison")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-01-01")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Load data
    symbols = list({p["symbol"] for p in PORTFOLIO})
    data_dict = {}
    print("Loading data...")
    for sym in symbols:
        try:
            data_dict[sym] = loader.load(sym, start=args.start, end=args.end)
            print(f"  {sym}: {len(data_dict[sym]):,} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")

    # ── Run 1: Static risk (current approach) ──
    print("\n" + "=" * 70)
    print("  RUN 1: STATIC RISK (2% fixed)")
    print("=" * 70)

    static_mgr = EquityManager()
    results_static = run_portfolio_with_manager(config, instruments, data_dict, static_mgr)

    combined_static = results_static["combined"]
    metrics_s = combined_static.metrics_summary()
    print(f"\n  Combined portfolio:")
    for k, v in metrics_s.items():
        print(f"    {k:<20s}: {v}")

    # ── Run 2: Dynamic challenge risk ──
    print("\n" + "=" * 70)
    print("  RUN 2: DYNAMIC CHALLENGE RISK")
    print("=" * 70)

    # Configure for Phase 1 (most demanding)
    challenge_config = ChallengeRiskConfig(
        profit_target=0.08,
        max_calendar_days=30,
        daily_dd_limit=0.05,
        max_dd_limit=0.10,
        base_risk=config.risk.risk_per_trade,
    )
    challenge_mgr = ChallengeEquityManager(challenge_config)
    results_dynamic = run_portfolio_with_manager(config, instruments, data_dict, challenge_mgr)

    combined_dynamic = results_dynamic["combined"]
    metrics_d = combined_dynamic.metrics_summary()
    print(f"\n  Combined portfolio:")
    for k, v in metrics_d.items():
        print(f"    {k:<20s}: {v}")

    # ── Sequential Challenge Comparison ──
    print("\n" + "=" * 70)
    print("  SEQUENTIAL CHALLENGE COMPARISON")
    print("=" * 70)

    simulator = PropFirmSimulator(config.prop_firm.phases)

    seq_static = simulator.sequential_challenge(combined_static, fee_per_attempt=500, cooldown_days=1)
    print_sequential_results("STATIC RISK (2% fixed)", seq_static)

    seq_dynamic = simulator.sequential_challenge(combined_dynamic, fee_per_attempt=500, cooldown_days=1)
    print_sequential_results("DYNAMIC CHALLENGE RISK", seq_dynamic)

    # ── Side-by-side summary ──
    print("\n" + "=" * 70)
    print("  SIDE-BY-SIDE COMPARISON")
    print("=" * 70)
    print(f"  {'Metric':<25s} {'Static 2%':>15s} {'Dynamic':>15s}")
    print(f"  {'-'*55}")

    for label, s, d in [
        ("Total trades", combined_static.total_trades, combined_dynamic.total_trades),
        ("Win rate", f"{combined_static.win_rate:.1f}%", f"{combined_dynamic.win_rate:.1f}%"),
        ("Profit factor", f"{combined_static.profit_factor:.2f}", f"{combined_dynamic.profit_factor:.2f}"),
        ("Total return", f"{combined_static.total_return_pct:.1f}%", f"{combined_dynamic.total_return_pct:.1f}%"),
        ("Max drawdown", f"{combined_static.max_drawdown_pct:.1f}%", f"{combined_dynamic.max_drawdown_pct:.1f}%"),
        ("Sharpe ratio", f"{combined_static.sharpe_ratio:.2f}", f"{combined_dynamic.sharpe_ratio:.2f}"),
    ]:
        print(f"  {label:<25s} {str(s):>15s} {str(d):>15s}")

    print(f"\n  {'Challenge metric':<25s} {'Static 2%':>15s} {'Dynamic':>15s}")
    print(f"  {'-'*55}")
    print(f"  {'Funded':.<25s} {'YES' if seq_static.funded else 'NO':>15s} {'YES' if seq_dynamic.funded else 'NO':>15s}")
    print(f"  {'P1 pass rate':.<25s} {seq_static.phase1_pass_rate:>14.0f}% {seq_dynamic.phase1_pass_rate:>14.0f}%")
    print(f"  {'P2 pass rate':.<25s} {seq_static.phase2_pass_rate:>14.0f}% {seq_dynamic.phase2_pass_rate:>14.0f}%")
    print(f"  {'Total fees':.<25s} {'$'+str(int(seq_static.total_fees)):>15s} {'$'+str(int(seq_dynamic.total_fees)):>15s}")
    if seq_static.total_calendar_days:
        s_days = str(seq_static.total_calendar_days)
    else:
        s_days = "N/A"
    if seq_dynamic.total_calendar_days:
        d_days = str(seq_dynamic.total_calendar_days)
    else:
        d_days = "N/A"
    print(f"  {'Days to funded':.<25s} {s_days:>15s} {d_days:>15s}")


if __name__ == "__main__":
    main()
