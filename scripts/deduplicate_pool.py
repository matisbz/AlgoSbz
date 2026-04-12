"""
Signal deduplication for combo pool.

Computes actual signal sets for all combos in the scan results and removes:
1. Pure subsets: combo A's signals are >80% subset of combo B's
2. Clones: two combos with >90% signal overlap on same instrument

For each redundant group, keeps the combo with highest PF.

Usage:
    python -X utf8 scripts/deduplicate_pool.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import pandas as pd
from collections import defaultdict

from algosbz.core.enums import SignalAction
from algosbz.data.loader import DataLoader
from algosbz.data.resampler import resample

# Import the scan results (auto-generated snippet)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cache"))

# We'll exec the snippet to get ALL_COMBOS
snippet_path = Path(__file__).resolve().parent.parent / "cache" / "all_combos_snippet.py"
snippet_ns = {}
exec(snippet_path.read_text(encoding="utf-8"), snippet_ns)
ALL_COMBOS = snippet_ns["ALL_COMBOS"]

# Strategy registry (all 21 strategies)
STRAT_REGISTRY = {
    "VMR": {"module": "algosbz.strategy.volatility_mean_reversion", "class": "VolatilityMeanReversion"},
    "TPB": {"module": "algosbz.strategy.trend_pullback", "class": "TrendPullback"},
    "SwBrk": {"module": "algosbz.strategy.swing_breakout", "class": "SwingBreakout"},
    "IBB": {"module": "algosbz.strategy.inside_bar_breakout", "class": "InsideBarBreakout"},
    "Engulf": {"module": "algosbz.strategy.engulfing_reversal", "class": "EngulfingReversal"},
    "StrBrk": {"module": "algosbz.strategy.structure_break", "class": "StructureBreak"},
    "MomDiv": {"module": "algosbz.strategy.momentum_divergence", "class": "MomentumDivergence"},
    "RegVMR": {"module": "algosbz.strategy.regime_vmr", "class": "RegimeAdaptiveVMR"},
    "EMArib": {"module": "algosbz.strategy.ema_ribbon_trend", "class": "EMARibbonTrend"},
    "SessBrk": {"module": "algosbz.strategy.session_breakout_v2", "class": "SessionBreakout"},
    "SMCOB": {"module": "algosbz.strategy.smc_order_block", "class": "SMCOrderBlock"},
    "FVGrev": {"module": "algosbz.strategy.fvg_reversion", "class": "FVGReversion"},
    "VWAPrev": {"module": "algosbz.strategy.vwap_reversion", "class": "VWAPReversion"},
    "MACross": {"module": "algosbz.strategy.ma_crossover", "class": "MACrossover"},
    "RSIext": {"module": "algosbz.strategy.rsi_extreme", "class": "RSIExtreme"},
    "KeltSq": {"module": "algosbz.strategy.keltner_squeeze", "class": "KeltnerSqueeze"},
    "MACDhist": {"module": "algosbz.strategy.macd_histogram", "class": "MACDHistogram"},
    "StochRev": {"module": "algosbz.strategy.stochastic_reversal", "class": "StochasticReversal"},
    "CCIext": {"module": "algosbz.strategy.cci_extreme", "class": "CCIExtreme"},
    "PinBar": {"module": "algosbz.strategy.pin_bar", "class": "PinBarReversal"},
    "DonTrend": {"module": "algosbz.strategy.donchian_trend", "class": "DonchianTrend"},
    "ADXbirth": {"module": "algosbz.strategy.adx_trend_birth", "class": "ADXTrendBirth"},
}

SUBSET_THRESHOLD = 0.80    # >80% overlap = redundant
CLONE_THRESHOLD = 0.90     # >90% symmetric overlap = clone


def make_strategy(combo_name):
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def get_signal_set(strategy, df):
    """Return set of (timestamp, action_name) for all entry signals."""
    strategy.setup(df)
    signals = set()
    for i in range(len(df)):
        bar = df.iloc[i]
        signal = strategy.on_bar(i, bar, False)
        if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            signals.add((df.index[i], signal.action.name))
    return signals


def main():
    loader = DataLoader()
    data_cache = {}

    print(f"Pool: {len(ALL_COMBOS)} combos from scan results")
    print(f"Computing signal sets...\n")

    # Group by (symbol, timeframe) to load data once
    combos_by_feed = defaultdict(list)
    for name, entry in ALL_COMBOS.items():
        strat = make_strategy(name)
        tf = strat.required_timeframe()
        combos_by_feed[(entry["symbol"], tf)].append(name)

    signal_sets = {}
    for (symbol, tf), combo_list in sorted(combos_by_feed.items()):
        cache_key = f"{symbol}_{tf}"
        if cache_key not in data_cache:
            try:
                raw = loader.load(symbol, start="2015-01-01", end="2025-01-01")
                data_cache[cache_key] = resample(raw, tf)
            except Exception as e:
                print(f"  SKIP {symbol} {tf}: {e}")
                continue
        df = data_cache[cache_key]
        print(f"  {symbol} {tf}: {len(df)} bars, {len(combo_list)} combos")

        for name in combo_list:
            strat = make_strategy(name)
            sigs = get_signal_set(strat, df)
            signal_sets[name] = sigs
            print(f"    {name}: {len(sigs)} signals")

    # Find redundancies
    print(f"\n{'='*80}")
    print(f"Checking for redundancies (subset>{SUBSET_THRESHOLD*100:.0f}%, clone>{CLONE_THRESHOLD*100:.0f}%)")
    print(f"{'='*80}\n")

    # Group combos by symbol (only same-instrument combos can be redundant)
    combos_by_symbol = defaultdict(list)
    for name in signal_sets:
        combos_by_symbol[ALL_COMBOS[name]["symbol"]].append(name)

    to_remove = set()
    removal_reasons = {}

    for symbol, combos in sorted(combos_by_symbol.items()):
        for i, c1 in enumerate(combos):
            if c1 in to_remove:
                continue
            s1 = signal_sets[c1]
            if not s1:
                continue

            for j in range(i + 1, len(combos)):
                c2 = combos[j]
                if c2 in to_remove:
                    continue
                s2 = signal_sets[c2]
                if not s2:
                    continue

                overlap = len(s1 & s2)
                min_size = min(len(s1), len(s2))
                max_size = max(len(s1), len(s2))

                if min_size == 0:
                    continue

                # Subset check: smaller is >80% contained in larger
                subset_ratio = overlap / min_size

                if subset_ratio >= SUBSET_THRESHOLD:
                    # Remove the one with lower PF
                    pf1 = ALL_COMBOS[c1]["pf"]
                    pf2 = ALL_COMBOS[c2]["pf"]

                    if pf1 >= pf2:
                        victim = c2
                        survivor = c1
                    else:
                        victim = c1
                        survivor = c2

                    if victim not in to_remove:
                        kind = "CLONE" if subset_ratio >= CLONE_THRESHOLD else "SUBSET"
                        to_remove.add(victim)
                        removal_reasons[victim] = (
                            f"{kind} of {survivor} "
                            f"({overlap}/{min_size}={subset_ratio:.0%}, "
                            f"PF {ALL_COMBOS[victim]['pf']:.2f} vs {ALL_COMBOS[survivor]['pf']:.2f})"
                        )
                        print(f"  REMOVE {victim}")
                        print(f"    {removal_reasons[victim]}")

    # Build clean pool
    clean = {k: v for k, v in ALL_COMBOS.items() if k not in to_remove}

    print(f"\n{'='*80}")
    print(f"RESULTS")
    print(f"{'='*80}")
    print(f"  Original pool: {len(ALL_COMBOS)} combos")
    print(f"  Removed:       {len(to_remove)} redundant")
    print(f"  Clean pool:    {len(clean)} combos")

    # Strategy distribution in clean pool
    strat_counts = defaultdict(int)
    symbol_counts = defaultdict(int)
    for name, entry in clean.items():
        strat_counts[entry["strat"]] += 1
        symbol_counts[entry["symbol"]] += 1

    print(f"\n  Strategy distribution:")
    for s, c in sorted(strat_counts.items(), key=lambda x: -x[1]):
        print(f"    {s:12s}: {c}")

    print(f"\n  Symbol distribution:")
    for s, c in sorted(symbol_counts.items(), key=lambda x: -x[1]):
        print(f"    {s:10s}: {c}")

    # Write clean pool file
    output = Path("scripts/challenge_decks_v7_expanded.py")
    with output.open("w", encoding="utf-8") as f:
        f.write('"""\n')
        f.write(f'Clean combo pool v7 — {len(clean)} combos from massive scan.\n')
        f.write(f'Signal-deduplicated: {len(to_remove)} redundant combos removed.\n')
        f.write(f'Threshold: subset>{SUBSET_THRESHOLD*100:.0f}%, clone>{CLONE_THRESHOLD*100:.0f}%\n')
        f.write(f'Generated: 2026-04-12\n')
        f.write('"""\n\n')

        f.write("STRAT_REGISTRY = {\n")
        # Only include strats that have combos
        used_strats = set(entry["strat"] for entry in clean.values())
        for strat_name, info in sorted(STRAT_REGISTRY.items()):
            if strat_name in used_strats:
                f.write(f'    "{strat_name}": {{"module": "{info["module"]}", "class": "{info["class"]}"}},\n')
        f.write("}\n\n")

        f.write("ALL_COMBOS = {\n")
        for name, entry in sorted(clean.items(), key=lambda x: (-x[1]["pf"], x[0])):
            f.write(f'    "{name}": {{\n')
            f.write(f'        "strat": "{entry["strat"]}", "symbol": "{entry["symbol"]}", '
                    f'"tier": "{entry["tier"]}", "pf": {entry["pf"]},\n')
            f.write(f'        "params": {repr(entry["params"])},\n')
            f.write(f'    }},\n')
        f.write("}\n")

    print(f"\n  Saved to {output}")


if __name__ == "__main__":
    main()
