"""
Cross-validate backtest results: Darwinex/Dukascopy data vs FTMO MT5 data.

Runs each deck combo on 2024 data from both sources and compares:
1. Signal count and timing (should be nearly identical)
2. PF, win rate, total return (should be close)
3. Trade-level PnL comparison

Usage:
    python -X utf8 scripts/crossval_data_sources.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import pandas as pd
import numpy as np
from copy import deepcopy

from algosbz.core.config import load_config, load_all_instruments
from algosbz.core.enums import SignalAction
from algosbz.data.loader import DataLoader
from algosbz.data.resampler import resample
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig

from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY

DATA_DIR = Path(__file__).resolve().parent.parent / "Datos_historicos"
START, END = "2024-01-01", "2025-01-01"

# Map internal symbol to source file suffixes
DARWINEX_SOURCES = {
    "EURUSD": "Darwinex", "GBPJPY": "Darwinex", "USDCHF": "Darwinex",
    "XAUUSD": "Darwinex", "XTIUSD": "Darwinex",
    "AUDUSD": "Dukascopy", "NZDUSD": "Dukascopy", "EURJPY": "Dukascopy",
}

DECK = [
    "VMR_NZDUSD_wideR_H4_ny", "MACross_XAUUSD_trend_H4_ny", "IBB_NZDUSD_trend_H4",
    "Engulf_EURUSD_trend_H4", "ADXbirth_XTIUSD_slow_ema_H4", "EMArib_EURJPY_tight_H1",
    "MACross_AUDUSD_megaT_H4", "MACross_XAUUSD_wideR_H4_ny", "PinBar_EURJPY_deep_H4",
    "StrBrk_GBPJPY_wideR_H4", "VMR_USDCHF_default_H1_ny", "MomDiv_AUDUSD_wideR_H4",
    "StochRev_AUDUSD_calm_H4", "VMR_USDJPY_wideR_H4_ny", "EMArib_AUDUSD_trend_H4_lon",
    "MACross_EURUSD_wideR_H4_lon", "RegVMR_NZDUSD_default_H1_ny", "MACDhist_EURJPY_trend_H4",
    "MACross_USDCHF_trend_H4_ny", "RSIext_EURJPY_wideR_H4", "TPB_XTIUSD_trend_H4_ny",
    "MACross_USDJPY_wideR_H4_lon", "MACross_USDCHF_megaT_H4", "MACross_NZDUSD_trend_H4_lon",
    "TPB_NZDUSD_loose_H4_ny",
]


def load_ftmo_data(symbol: str) -> pd.DataFrame:
    """Load FTMO 2024 data the same way DataLoader does."""
    csv_path = DATA_DIR / f"{symbol}_M1_FTMO_2024.csv"
    if not csv_path.exists():
        return pd.DataFrame()

    df = pd.read_csv(csv_path)
    # Match DataLoader column naming
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")

    # Rename columns to match standard format
    rename = {}
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            for c in df.columns:
                if c.lower() == col:
                    rename[c] = col
    if rename:
        df = df.rename(columns=rename)

    if "tick_volume" in df.columns:
        df = df.rename(columns={"tick_volume": "volume"})

    # Convert spread from points to price (same as DataLoader)
    # MT5 spread is in points (smallest price increment)
    from algosbz.core.config import load_all_instruments
    instruments = load_all_instruments()
    inst = instruments.get(symbol)
    if inst and "spread" in df.columns:
        df["spread"] = df["spread"].astype(float) * inst.point_size

    # Filter to 2024
    df = df[(df.index >= pd.Timestamp(START)) & (df.index < pd.Timestamp(END))]
    return df


def run_backtest(config, inst, data, combo_name):
    """Run backtest and return result + trade list."""
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    strategy = cls(entry["params"])

    cfg = deepcopy(config)
    cfg.risk.risk_per_trade = 0.02
    cfg.risk.daily_dd_limit = 0.50
    cfg.risk.max_dd_limit = 0.50
    eq_cfg = EquityManagerConfig(
        dd_tiers=[(0.50, 1.0)], daily_stop_threshold=0.50,
        progressive_trades=0, consecutive_win_bonus=0,
    )
    engine = BacktestEngine(cfg, inst, EquityManager(eq_cfg))
    result = engine.run(strategy, data, entry["symbol"])
    return result


def get_signals(data, combo_name):
    """Get raw signals (no position sizing) for comparison."""
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    strategy = cls(entry["params"])
    strategy.setup(data)

    signals = []
    for i in range(len(data)):
        s = strategy.on_bar(i, data.iloc[i], False)
        if s.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            signals.append((data.index[i], s.action.name))
    return signals


def main():
    config = load_config()
    instruments = load_all_instruments()
    loader = DataLoader()

    # Skip USDJPY — no FTMO 2024 file and no Darwinex data past Oct 2025
    skip_symbols = {"USDJPY"}

    # Load data from both sources
    print("Loading data from both sources...\n")
    darwinex_data = {}
    ftmo_data = {}

    symbols_needed = set()
    for combo in DECK:
        sym = ALL_COMBOS[combo]["symbol"]
        if sym not in skip_symbols:
            symbols_needed.add(sym)

    for sym in sorted(symbols_needed):
        # Darwinex/Dukascopy (standard loader)
        try:
            raw = loader.load(sym, start=START, end=END)
            darwinex_data[sym] = raw
            print(f"  {sym} Darwinex: {len(raw):,} M1 bars")
        except Exception as e:
            print(f"  {sym} Darwinex: FAILED - {e}")

        # FTMO
        ftmo = load_ftmo_data(sym)
        if not ftmo.empty:
            ftmo_data[sym] = ftmo
            print(f"  {sym} FTMO:    {len(ftmo):,} M1 bars")
        else:
            print(f"  {sym} FTMO:    NO DATA")

    # Compare per combo
    print(f"\n{'='*100}")
    print(f"  CROSS-VALIDATION: Darwinex/Dukascopy vs FTMO MT5 — 2024")
    print(f"{'='*100}\n")

    results = []
    for combo in DECK:
        entry = ALL_COMBOS[combo]
        sym = entry["symbol"]
        if sym in skip_symbols:
            continue
        if sym not in darwinex_data or sym not in ftmo_data:
            continue

        info = STRAT_REGISTRY[entry["strat"]]
        mod = importlib.import_module(info["module"])
        cls = getattr(mod, info["class"])
        strat = cls(entry["params"])
        tf = strat.required_timeframe()

        # Resample both sources
        dw_resampled = resample(darwinex_data[sym], tf)
        ft_resampled = resample(ftmo_data[sym], tf)

        # Filter to 2024 only
        dw_2024 = dw_resampled[(dw_resampled.index >= START) & (dw_resampled.index < END)]
        ft_2024 = ft_resampled[(ft_resampled.index >= START) & (ft_resampled.index < END)]

        # Get signals
        dw_sigs = get_signals(dw_2024, combo)
        ft_sigs = get_signals(ft_2024, combo)

        # Run backtests
        inst = instruments[sym]
        try:
            dw_result = run_backtest(config, inst, dw_2024, combo)
            ft_result = run_backtest(config, inst, ft_2024, combo)
        except Exception as e:
            print(f"  {combo}: ERROR - {e}")
            continue

        # Compare signals
        dw_sig_times = set(s[0] for s in dw_sigs)
        ft_sig_times = set(s[0] for s in ft_sigs)
        common = dw_sig_times & ft_sig_times
        only_dw = dw_sig_times - ft_sig_times
        only_ft = ft_sig_times - dw_sig_times
        sig_match_pct = len(common) / max(len(dw_sig_times), len(ft_sig_times), 1) * 100

        # Compare results
        dw_pf = dw_result.profit_factor if dw_result.total_trades > 0 else 0
        ft_pf = ft_result.profit_factor if ft_result.total_trades > 0 else 0
        pf_diff = abs(dw_pf - ft_pf)

        status = "OK" if sig_match_pct >= 90 and pf_diff < 0.3 else "WARN" if sig_match_pct >= 70 else "FAIL"

        print(f"  {status:4s} {combo:40s} "
              f"Sigs: DW={len(dw_sigs):3d} FT={len(ft_sigs):3d} match={sig_match_pct:5.1f}% "
              f"PF: DW={dw_pf:.2f} FT={ft_pf:.2f} (d={pf_diff:.2f}) "
              f"Trades: DW={dw_result.total_trades} FT={ft_result.total_trades}")

        if only_dw:
            print(f"         only_DW: {sorted(only_dw)[:3]}")
        if only_ft:
            print(f"         only_FT: {sorted(only_ft)[:3]}")

        results.append({
            "combo": combo, "status": status,
            "dw_sigs": len(dw_sigs), "ft_sigs": len(ft_sigs),
            "match_pct": sig_match_pct,
            "dw_pf": dw_pf, "ft_pf": ft_pf, "pf_diff": pf_diff,
            "dw_trades": dw_result.total_trades, "ft_trades": ft_result.total_trades,
        })

    # Summary
    print(f"\n{'='*100}")
    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    fail = sum(1 for r in results if r["status"] == "FAIL")
    avg_match = np.mean([r["match_pct"] for r in results]) if results else 0
    avg_pf_diff = np.mean([r["pf_diff"] for r in results]) if results else 0

    print(f"  SUMMARY: {len(results)} combos | OK={ok} WARN={warn} FAIL={fail}")
    print(f"  Avg signal match: {avg_match:.1f}%")
    print(f"  Avg PF difference: {avg_pf_diff:.3f}")
    print(f"{'='*100}")


if __name__ == "__main__":
    main()
