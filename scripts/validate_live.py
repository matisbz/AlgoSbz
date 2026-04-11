"""
Live vs Backtest Validator — verifies that live trading matches backtest behavior.

Reads the live trade log (trade_history.jsonl), downloads the same data period
from MT5, runs the backtest engine over it, and compares trade-by-trade.

Any discrepancy means the live system is NOT faithful to the backtest and must
be investigated before continuing.

Usage:
    python -X utf8 scripts/validate_live.py              (uses MT5, default)
    python -X utf8 scripts/validate_live.py --days 7     (last 7 days only)
    python -X utf8 scripts/validate_live.py --offline     (use local data files)
"""
import sys
import json
import logging
import argparse
import importlib
from pathlib import Path
from datetime import datetime, timedelta
from copy import deepcopy
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import pandas as pd

from algosbz.core.config import load_config, load_all_instruments
from algosbz.core.enums import SignalAction
from algosbz.core.models import Signal
from algosbz.data.resampler import resample
from algosbz.backtest.engine import BacktestEngine
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig
from algosbz.live.runtime import ensure_aware_utc, utc_now

from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"
TRADE_LOG = Path(__file__).resolve().parent.parent / "data" / "trade_history.jsonl"


def load_live_trades(days: int = None) -> list[dict]:
    """Load entry events from the live trade log."""
    if not TRADE_LOG.exists():
        logger.error("No trade log found at %s", TRADE_LOG)
        return []

    trades = []
    ignored = 0
    cutoff = None
    if days:
        cutoff = ensure_aware_utc(utc_now() - timedelta(days=days)).replace(tzinfo=None)

    with open(TRADE_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            ts = datetime.fromisoformat(t["ts"])
            if ts.tzinfo is not None:
                ts = ensure_aware_utc(ts).replace(tzinfo=None)
            if cutoff and ts < cutoff:
                continue
            event = t.get("event")
            direction = t.get("direction")

            is_entry_event = event in {"OPEN", "OPEN_RECOVERED"}
            is_legacy_entry = event is None and direction in {"BUY", "SELL"}
            if not (is_entry_event or is_legacy_entry):
                ignored += 1
                continue

            if t.get("fill_price", 0.0) in (0, 0.0, None):
                ignored += 1
                continue

            t["_ts"] = ts
            trades.append(t)

    if ignored:
        logger.info("Ignored %d non-entry/invalid log events", ignored)
    return trades


def run_backtest_signals(combo_name: str, data: pd.DataFrame,
                         config, instrument) -> list[dict]:
    """
    Run a strategy through the backtest engine and capture all signals generated.

    Returns list of {bar_time, action, sl, tp, ref_price, fill_bar_time, fill_price}.
    """
    entry = ALL_COMBOS[combo_name]
    info = STRAT_REGISTRY[entry["strat"]]
    mod = importlib.import_module(info["module"])
    cls = getattr(mod, info["class"])
    strategy = cls(entry["params"])

    tf = strategy.required_timeframe()
    df = resample(data, tf)

    if df.empty:
        return []

    strategy.setup(df)

    signals = []
    pending = None

    for i in range(len(df)):
        bar = df.iloc[i]
        bar_time = df.index[i]

        # Execute pending signal at this bar's open (matches backtest engine)
        if pending is not None:
            ref_price = pending["signal"].metadata.get("ref_price")
            fill_price = bar["open"]
            gap = (fill_price - ref_price) if ref_price else 0

            adjusted_sl = pending["signal"].stop_loss
            adjusted_tp = pending["signal"].take_profit
            if ref_price and pending["signal"].stop_loss is not None:
                adjusted_sl = pending["signal"].stop_loss + gap
                adjusted_tp = (pending["signal"].take_profit + gap) if pending["signal"].take_profit else None

            signals.append({
                "signal_bar": pending["bar_time"],
                "fill_bar": bar_time,
                "fill_price": fill_price,
                "action": pending["signal"].action.name,
                "sl": adjusted_sl,
                "tp": adjusted_tp,
                "ref_price": ref_price,
                "gap": gap,
            })
            pending = None

        # Generate signal on this bar
        signal = strategy.on_bar(i, bar, has_position=False)
        if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            pending = {"signal": signal, "bar_time": bar_time}

    return signals


def main():
    parser = argparse.ArgumentParser(description="Validate live vs backtest")
    parser.add_argument("--days", type=int, default=None,
                        help="Only validate last N days (default: all)")
    parser.add_argument("--offline", action="store_true",
                        help="Use local data files instead of MT5 (only works if data covers the live period)")
    args = parser.parse_args()

    # Load config
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    symbol_map = cfg.get("symbol_map", {})
    app_config = load_config()
    instruments = load_all_instruments()

    # Load live trades
    live_trades = load_live_trades(args.days)
    if not live_trades:
        logger.info("No live trades to validate.")
        return

    logger.info("Loaded %d live trades", len(live_trades))

    # Group live trades by combo
    live_by_combo = defaultdict(list)
    for t in live_trades:
        live_by_combo[t["combo"]].append(t)

    # Determine date range
    all_dates = [t["_ts"] for t in live_trades]
    start_date = min(all_dates) - timedelta(days=30)  # extra history for setup
    end_date = max(all_dates) + timedelta(days=1)

    logger.info("Date range: %s → %s", start_date.date(), end_date.date())

    trade_combos = sorted(c for c in live_by_combo if c in ALL_COMBOS)
    if not trade_combos:
        logger.error("No logged combos map to current combo registry.")
        return

    # Load data — MT5 by default (live trades only exist when MT5 is available)
    # --offline fallback only works if local data covers the live period
    if args.offline:
        from algosbz.data.loader import DataLoader
        loader = DataLoader()
        data_dict = {}
        all_symbols = list({ALL_COMBOS[c]["symbol"] for c in trade_combos})
        for sym in all_symbols:
            try:
                data_dict[sym] = loader.load(sym, start=str(start_date.date()),
                                             end=str(end_date.date()))
            except Exception as e:
                logger.warning("Failed to load %s: %s", sym, e)
    else:
        from algosbz.live.mt5_connector import MT5Connector
        first_acct = next(
            acct for acct in cfg["accounts"]
            if acct.get("enabled", True) and acct.get("login", 0) != 0
        )
        conn = MT5Connector(first_acct["login"], first_acct["password"],
                           first_acct["server"], symbol_map)
        if not conn.connect():
            logger.error("MT5 connection failed — use --offline if you have local data")
            return

        data_dict = {}
        all_symbols = list({ALL_COMBOS[c]["symbol"] for c in trade_combos})
        for sym in all_symbols:
            # Get enough bars to cover the period
            for tf in ["M15", "H1", "H4"]:
                df = conn.get_bars(sym, tf, 5000)
                if not df.empty:
                    data_dict[sym] = df
                    break
        conn.disconnect()

    # Run backtest for each combo that has live trades
    print(f"\n{'='*100}")
    print(f"  LIVE vs BACKTEST VALIDATION")
    print(f"{'='*100}")

    total_matches = 0
    total_mismatches = 0
    total_live_only = 0
    total_bt_only = 0

    for combo in trade_combos:
        sym = ALL_COMBOS[combo]["symbol"]
        if sym not in data_dict:
            logger.warning("No data for %s (%s), skipping", combo, sym)
            continue

        # Run backtest signals
        bt_signals = run_backtest_signals(combo, data_dict[sym],
                                          app_config, instruments[sym])

        live_combo = live_by_combo[combo]

        # Filter backtest signals to the live period
        live_start = min(t["_ts"] for t in live_combo)
        live_end = max(t["_ts"] for t in live_combo)
        bt_in_period = [s for s in bt_signals
                        if live_start - timedelta(hours=12) <= s["fill_bar"] <= live_end + timedelta(hours=12)]

        print(f"\n  {'─'*80}")
        print(f"  {combo}: {len(live_combo)} live trades, {len(bt_in_period)} backtest signals")
        print(f"  {'─'*80}")

        # Match by time proximity (within same bar)
        matched = []
        unmatched_live = list(live_combo)
        unmatched_bt = list(bt_in_period)

        for bt_sig in bt_in_period:
            best_match = None
            best_delta = timedelta(hours=24)

            for lt in unmatched_live:
                delta = abs(lt["_ts"] - bt_sig["fill_bar"])
                if delta < best_delta:
                    best_delta = delta
                    best_match = lt

            if best_match and best_delta < timedelta(hours=8):
                # Check direction match
                live_dir = best_match["direction"]
                bt_dir = "BUY" if "LONG" in bt_sig["action"] else "SELL"
                dir_match = live_dir == bt_dir

                # Check SL proximity
                sl_diff = abs(best_match["sl"] - bt_sig["sl"]) if bt_sig["sl"] else 0
                sl_match = sl_diff < 0.01 * abs(bt_sig["sl"]) if bt_sig["sl"] else True

                matched.append({
                    "live": best_match,
                    "bt": bt_sig,
                    "time_delta": best_delta,
                    "dir_match": dir_match,
                    "sl_diff": sl_diff,
                    "sl_match": sl_match,
                })
                unmatched_live.remove(best_match)
                unmatched_bt.remove(bt_sig)

        # Print matches
        for m in matched:
            lt = m["live"]
            bt = m["bt"]
            status = "OK" if m["dir_match"] and m["sl_match"] else "MISMATCH"
            if status == "OK":
                total_matches += 1
            else:
                total_mismatches += 1

            dir_icon = "==" if m["dir_match"] else "!="
            sl_icon = "~=" if m["sl_match"] else "!="

            print(f"    [{status:>8s}] {bt['fill_bar']} "
                  f"Dir: {lt['direction']}{dir_icon}{bt['action'][:5]} "
                  f"SL: {lt['sl']:.5f}{sl_icon}{bt['sl']:.5f} "
                  f"dt={m['time_delta']}")

        # Print unmatched
        for lt in unmatched_live:
            total_live_only += 1
            print(f"    [LIVE ONLY] {lt['_ts']} {lt['direction']} "
                  f"SL={lt['sl']:.5f} (no backtest match)")

        for bt in unmatched_bt:
            total_bt_only += 1
            print(f"    [BT ONLY  ] {bt['fill_bar']} {bt['action'][:5]} "
                  f"SL={bt['sl']:.5f} (not taken in live)")

    # Summary
    print(f"\n{'='*100}")
    print(f"  VALIDATION SUMMARY")
    print(f"{'='*100}")
    total = total_matches + total_mismatches + total_live_only + total_bt_only
    print(f"\n  Matched & correct:  {total_matches:>4d}")
    print(f"  Matched & MISMATCH: {total_mismatches:>4d}")
    print(f"  Live only (extra):  {total_live_only:>4d}")
    print(f"  Backtest only:      {total_bt_only:>4d}")
    print(f"  Total events:       {total:>4d}")

    if total_mismatches == 0 and total_live_only == 0 and total_bt_only == 0:
        print(f"\n  PERFECT MATCH — live is faithful to backtest")
    elif total_mismatches == 0:
        pct = total_matches / max(total, 1) * 100
        print(f"\n  SIGNALS CORRECT ({pct:.0f}% matched) — "
              f"some timing differences (portfolio controls, has_position)")
        print(f"  This is expected: live has per-account controls that raw backtest doesn't.")
    else:
        print(f"\n  WARNING: {total_mismatches} MISMATCHES FOUND")
        print(f"  The live system is NOT matching the backtest. Investigate before continuing!")

    # Detailed explanation of expected differences
    print(f"\n  EXPECTED DIFFERENCES (not bugs):")
    print(f"    - 'LIVE ONLY': live took a trade that backtest wouldn't at exact same bar")
    print(f"      → Usually timing: live checked a few seconds earlier/later")
    print(f"    - 'BT ONLY': backtest would have taken trade but live didn't")
    print(f"      → Usually: portfolio controls blocked it (daily cap, cooldown, has_position)")
    print(f"      → Or: live was offline / MT5 disconnected")
    print(f"    - 'MISMATCH': same trade but different direction or SL")
    print(f"      → This is a REAL BUG — should not happen if faithful to backtest")


if __name__ == "__main__":
    main()
