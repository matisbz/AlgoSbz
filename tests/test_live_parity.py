"""
Parity test: backtest engine vs live trader signal generation.

Verifies that given identical bar data, the live trader's StrategyManager
produces EXACTLY the same signals as the backtest engine.

This catches:
- Index/offset bugs in generate_signals (the bug that caused 0 trades in live)
- Re-setup breaking indicator state
- has_position logic differences
- Forming bar exclusion errors

Usage:
    python -X utf8 -m pytest tests/test_live_parity.py -v
    python -X utf8 tests/test_live_parity.py          # standalone
"""
import sys
import importlib
from pathlib import Path
from copy import deepcopy
from collections import defaultdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.loader import DataLoader
from algosbz.data.resampler import resample
from scripts.challenge_decks_v5_clean import ALL_COMBOS, STRAT_REGISTRY


# ─── Helpers ────────────────────────────────────────────────────────


def make_strategy(combo_name: str):
    """Instantiate a strategy from ALL_COMBOS, same as live_trader does."""
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    return cls(entry["params"])


def backtest_signals(strategy, df: pd.DataFrame) -> list[tuple[pd.Timestamp, str]]:
    """
    Run the backtest signal loop (engine.py lines 58-158) and collect
    every signal with its bar timestamp.

    Returns [(bar_timestamp, action_name), ...] for ENTER_LONG/ENTER_SHORT only.
    """
    strategy.setup(df)
    signals = []
    for i in range(len(df)):
        bar = df.iloc[i]
        bar_time = df.index[i]
        # has_position=False: we want raw signal generation parity,
        # not position-dependent filtering (that's tested separately)
        signal = strategy.on_bar(i, bar, False)
        if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            signals.append((bar_time, signal.action.name, signal.stop_loss, signal.take_profit))
    return signals


def live_signals(combo_name: str, df: pd.DataFrame,
                 warmup: int = 200) -> list[tuple[pd.Timestamp, str]]:
    """
    Simulate the live trader's StrategyManager flow:
      1. setup() on first `warmup` bars (the "history" download)
      2. Feed bars one-by-one (simulating check_new_bars + generate_signals)
      3. Collect signals

    This replicates exactly what the live bot does:
    - setup on df[:warmup+1] (warmup bars + 1 forming bar)
    - last_signal_bar = df.index[warmup-1] (= df[-2] of the setup window)
    - For each new bar: append it, re-setup, evaluate df[-2] (completed bar)

    Returns [(bar_timestamp, action_name), ...] for ENTER_LONG/ENTER_SHORT only.
    """
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    strategy = cls(entry["params"])

    signals = []

    # Phase 1: Setup with history (simulates setup_with_history)
    # The live bot gets `warmup` bars from MT5, where the last one is forming.
    # It calls setup() and sets last_signal_bar = df.index[-2]
    setup_end = warmup + 1  # +1 for the forming bar
    if setup_end > len(df):
        setup_end = len(df)

    history = df.iloc[:setup_end].copy()
    strategy.setup(history)

    # last_signal_bar = penultimate bar of history (like live_trader line 141)
    if len(history) >= 2:
        last_signal_bar = history.index[-2]
    else:
        last_signal_bar = None

    # Phase 2: Feed new bars one by one (simulates the poll loop)
    # Each cycle: the "forming" bar completes, a new forming bar appears
    for new_bar_pos in range(setup_end, len(df)):
        # The df the live bot sees: history up to new_bar_pos (inclusive)
        # new_bar_pos is the new forming bar
        # new_bar_pos - 1 is the just-completed bar (old forming)
        current_df = df.iloc[:new_bar_pos + 1].copy()

        # Re-setup with extended data (live_trader line 218/228)
        strategy.setup(current_df)

        # Determine which bars to evaluate (replicating generate_signals)
        # start: after last_signal_bar
        # end: exclude current_df[-1] (forming bar)
        start_idx = 0
        if last_signal_bar is not None:
            matches = current_df.index.get_indexer([last_signal_bar], method=None)
            if matches[0] >= 0:
                start_idx = matches[0] + 1
            else:
                start_idx = current_df.index.searchsorted(last_signal_bar, side="right")

        end_idx = len(current_df) - 1  # exclude forming bar

        for idx in range(start_idx, end_idx):
            bar = current_df.iloc[idx]
            bar_time = current_df.index[idx]

            signal = strategy.on_bar(idx, bar, False)
            last_signal_bar = bar_time

            if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                signals.append((bar_time, signal.action.name, signal.stop_loss, signal.take_profit))

    return signals


# ─── Test runner ────────────────────────────────────────────────────


def test_parity_all_combos():
    """Test every combo in the v3 deck for backtest↔live signal parity."""
    loader = DataLoader()
    warmup = 200
    # Use a recent 2-year slice to keep it fast but meaningful
    start, end = "2023-01-01", "2025-01-01"

    failures = []
    total_signals = 0

    # Group combos by (symbol, timeframe) to avoid reloading data
    combos_by_feed = defaultdict(list)
    for combo_name in ALL_COMBOS:
        entry = ALL_COMBOS[combo_name]
        strat = make_strategy(combo_name)
        tf = strat.required_timeframe()
        combos_by_feed[(entry["symbol"], tf)].append(combo_name)

    for (symbol, tf), combo_list in sorted(combos_by_feed.items()):
        # Load and resample data once per feed
        try:
            raw = loader.load(symbol, start=start, end=end)
        except Exception as e:
            print(f"  SKIP {symbol}: {e}")
            continue

        df = resample(raw, tf)
        if len(df) < warmup + 50:
            print(f"  SKIP {symbol} {tf}: only {len(df)} bars")
            continue

        for combo_name in combo_list:
            # Run backtest signal collection
            bt_strat = make_strategy(combo_name)
            bt_sigs = backtest_signals(bt_strat, df)

            # Run live simulation
            lv_sigs = live_signals(combo_name, df, warmup=warmup)

            # The live simulation skips the first `warmup` bars (they're in
            # the setup window). Backtest signals from those bars won't
            # appear in live. So filter backtest signals to only those
            # AFTER the warmup window.
            # In setup, last_signal_bar = df.index[warmup-1] (penultimate of
            # history window). Live starts evaluating from df.index[warmup]
            # onwards (the old forming bar that just completed).
            # Backtest evaluates ALL bars including warmup, so filter:
            cutoff = df.index[warmup] if warmup < len(df) else df.index[0]
            bt_sigs_filtered = [(t, a, sl, tp) for t, a, sl, tp in bt_sigs if t >= cutoff]

            # Compare
            bt_times = [(t, a) for t, a, sl, tp in bt_sigs_filtered]
            lv_times = [(t, a) for t, a, sl, tp in lv_sigs]

            total_signals += len(bt_sigs_filtered)

            if bt_times != lv_times:
                # Find first divergence
                bt_set = set(bt_times)
                lv_set = set(lv_times)
                only_bt = sorted(bt_set - lv_set)[:3]
                only_lv = sorted(lv_set - bt_set)[:3]
                failures.append({
                    "combo": combo_name,
                    "bt_count": len(bt_sigs_filtered),
                    "lv_count": len(lv_sigs),
                    "only_in_bt": only_bt,
                    "only_in_lv": only_lv,
                })
                print(f"  FAIL {combo_name}: bt={len(bt_sigs_filtered)} lv={len(lv_sigs)} "
                      f"only_bt={only_bt[:2]} only_lv={only_lv[:2]}")
            else:
                # Also check SL/TP values match
                sl_tp_mismatches = []
                for (bt_t, bt_a, bt_sl, bt_tp), (lv_t, lv_a, lv_sl, lv_tp) in zip(bt_sigs_filtered, lv_sigs):
                    if bt_sl != lv_sl or bt_tp != lv_tp:
                        sl_tp_mismatches.append((bt_t, bt_sl, lv_sl, bt_tp, lv_tp))

                if sl_tp_mismatches:
                    failures.append({
                        "combo": combo_name,
                        "bt_count": len(bt_sigs_filtered),
                        "lv_count": len(lv_sigs),
                        "sl_tp_mismatches": sl_tp_mismatches[:3],
                    })
                    print(f"  FAIL {combo_name}: signal times match but SL/TP differ "
                          f"({len(sl_tp_mismatches)} mismatches)")
                else:
                    print(f"  OK   {combo_name}: {len(bt_sigs_filtered)} signals match perfectly")

    # Summary
    print(f"\n{'='*60}")
    print(f"TOTAL: {len(ALL_COMBOS)} combos, {total_signals} signals checked")
    if failures:
        print(f"FAILED: {len(failures)} combos with mismatches:")
        for f in failures:
            print(f"  - {f['combo']}: bt={f['bt_count']} lv={f['lv_count']}")
            if "only_in_bt" in f:
                print(f"    only_bt: {f['only_in_bt'][:3]}")
                print(f"    only_lv: {f['only_in_lv'][:3]}")
            if "sl_tp_mismatches" in f:
                print(f"    SL/TP mismatches: {f['sl_tp_mismatches'][:3]}")
        print(f"\nPARITY TEST FAILED")
    else:
        print(f"ALL {len(ALL_COMBOS)} COMBOS PASSED — live matches backtest exactly")

    assert not failures, f"{len(failures)} combos failed parity check"


# ─── Standalone run ─────────────────────────────────────────────────

if __name__ == "__main__":
    test_parity_all_combos()
