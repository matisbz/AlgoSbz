"""Check last 7 days of trading signals with proper position tracking."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib
import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timedelta
from algosbz.core.enums import SignalAction
from scripts.challenge_decks import ALL_COMBOS, STRAT_REGISTRY
from algosbz.live.mt5_connector import MT5Connector

DECK = [
    "SessBrk_XTIUSD_M15", "SwBrk_SPY_slow_H4", "SMCOB_XAUUSD_loose_H4",
    "Engulf_XAUUSD_tight_H4", "TPB_XTIUSD_loose_H4", "TPB_XNGUSD_loose_H4",
    "RegVMR_XTIUSD_H1", "VMR_SPY_H4", "Engulf_EURUSD_tight_H4",
    "SwBrk_XTIUSD_H4", "VMR_USDCHF_H1", "RegVMR_XAUUSD_H1",
    "StrBrk_GBPJPY_slow_H4", "EMArib_XNGUSD_loose_H4", "SMCOB_XAUUSD_H4",
    "SwBrk_SPY_fast_H4",
]

SYMBOL_MAP = {
    "EURUSD": "EURUSD", "GBPJPY": "GBPJPY", "USDCHF": "USDCHF",
    "USDJPY": "USDJPY", "XAUUSD": "XAUUSD",
    "XTIUSD": "USOIL.cash", "XNGUSD": "NATGAS.cash", "SPY": "US500.cash",
}

PIP_INFO = {
    "EURUSD": {"pip": 0.0001, "pip_value": 10.0},
    "GBPJPY": {"pip": 0.01, "pip_value": 6.67},
    "USDCHF": {"pip": 0.0001, "pip_value": 11.24},
    "XAUUSD": {"pip": 0.01, "pip_value": 0.10},
    "XTIUSD": {"pip": 0.01, "pip_value": 1.0},
    "XNGUSD": {"pip": 0.001, "pip_value": 1.0},
    "SPY": {"pip": 0.01, "pip_value": 1.0},
}


def main():
    conn = MT5Connector(1512964593, "nIQHl?m7", "FTMO-Demo", SYMBOL_MAP)
    if not conn.connect():
        print("ERROR: cannot connect")
        return

    # Current prices
    current_prices = {}
    for sym, mt5_sym in SYMBOL_MAP.items():
        tick = mt5.symbol_info_tick(mt5_sym)
        if tick:
            current_prices[sym] = {"bid": tick.bid, "ask": tick.ask}

    # Download data
    feeds = {}
    for combo in DECK:
        entry = ALL_COMBOS[combo]
        tf = entry["params"].get("timeframe", "H4")
        key = (entry["symbol"], tf)
        feeds.setdefault(key, []).append(combo)

    bar_data = {}
    for (symbol, tf) in feeds:
        df = conn.get_bars(symbol, tf, 500)
        if not df.empty:
            bar_data[(symbol, tf)] = df

    conn.disconnect()

    start_date = (datetime.now() - timedelta(days=7)).date()
    end_date = datetime.now().date()

    all_results = []

    for combo in DECK:
        entry = ALL_COMBOS[combo]
        symbol = entry["symbol"]
        tf = entry["params"].get("timeframe", "H4")
        key = (symbol, tf)
        if key not in bar_data:
            continue

        df = bar_data[key]
        info_strat = STRAT_REGISTRY[entry["strat"]]
        mod = importlib.import_module(info_strat["module"])
        cls = getattr(mod, info_strat["class"])
        strategy = cls(entry["params"])
        strategy.setup(df)

        has_position = False
        trade = None

        for idx in range(len(df) - 1):
            bar = df.iloc[idx]
            bar_time = df.index[idx]

            # Check SL/TP on current bar
            if has_position and trade is not None:
                hit = False
                if trade["dir"] == "BUY":
                    if trade["sl"] and bar["low"] <= trade["sl"]:
                        trade["outcome"] = "SL"
                        trade["close_price"] = trade["sl"]
                        trade["close_time"] = bar_time
                        hit = True
                    elif trade["tp"] and bar["high"] >= trade["tp"]:
                        trade["outcome"] = "TP"
                        trade["close_price"] = trade["tp"]
                        trade["close_time"] = bar_time
                        hit = True
                else:
                    if trade["sl"] and bar["high"] >= trade["sl"]:
                        trade["outcome"] = "SL"
                        trade["close_price"] = trade["sl"]
                        trade["close_time"] = bar_time
                        hit = True
                    elif trade["tp"] and bar["low"] <= trade["tp"]:
                        trade["outcome"] = "TP"
                        trade["close_price"] = trade["tp"]
                        trade["close_time"] = bar_time
                        hit = True

                if hit:
                    has_position = False
                    if trade["signal_bar"].date() >= start_date:
                        all_results.append(trade)
                    trade = None

            signal = strategy.on_bar(idx, bar, has_position)

            if bar_time.date() < start_date:
                continue

            if not has_position and signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                direction = "BUY" if signal.action == SignalAction.ENTER_LONG else "SELL"
                next_bar = df.iloc[idx + 1]
                fill = next_bar["open"]
                sl = signal.stop_loss
                tp = signal.take_profit

                pip_info = PIP_INFO[symbol]
                sl_dist = abs(fill - sl) if sl else 0
                sl_pips = sl_dist / pip_info["pip"] if sl_dist > 0 else 1

                equity = 10000
                risk = 0.02
                mult = 0.50
                lot_size = (equity * risk * mult) / (sl_pips * pip_info["pip_value"])
                lot_size = round(max(0.01, min(lot_size, 10.0)), 2)

                trade = {
                    "combo": combo, "symbol": symbol, "dir": direction,
                    "signal_bar": bar_time, "fill": fill,
                    "sl": sl, "tp": tp, "lots": lot_size,
                    "outcome": None, "close_price": None, "close_time": None,
                }
                has_position = True

        # Still open at end
        if has_position and trade is not None and trade["signal_bar"].date() >= start_date:
            cp = current_prices.get(trade["symbol"], {})
            if trade["dir"] == "BUY":
                trade["close_price"] = cp.get("bid", trade["fill"])
            else:
                trade["close_price"] = cp.get("ask", trade["fill"])
            trade["outcome"] = "OPEN"
            trade["close_time"] = datetime.now()
            all_results.append(trade)

    all_results.sort(key=lambda x: x["signal_bar"])

    print(f"\n  Last 7 days: {start_date} to {end_date}")
    print(f"  Equity: $10,000 | Risk: 2% | Mult: 0.50 (anti-martingale)\n")
    hdr = f"  {'Signal Bar':<20s} {'Combo':<28s} {'Dir':>4s} {'Lots':>5s} {'Fill':>10s} {'Now/Close':>10s}  {'PnL $':>10s}  {'Status':>6s}"
    print(hdr)
    print(f"  {'-'*20} {'-'*28} {'-'*4} {'-'*5} {'-'*10} {'-'*10}  {'-'*10}  {'-'*6}")

    total_pnl = 0
    for t in all_results:
        pip_info = PIP_INFO[t["symbol"]]
        if t["dir"] == "BUY":
            pnl_pts = t["close_price"] - t["fill"]
        else:
            pnl_pts = t["fill"] - t["close_price"]

        pnl_pips = pnl_pts / pip_info["pip"]
        pnl_usd = pnl_pips * pip_info["pip_value"] * t["lots"]
        total_pnl += pnl_usd

        icon = "WIN" if t["outcome"] == "TP" else ("LOSS" if t["outcome"] == "SL" else "OPEN")
        print(f"  {str(t['signal_bar']):<20s} {t['combo']:<28s} {t['dir']:>4s} {t['lots']:>5.2f} "
              f"{t['fill']:>10.5f} {t['close_price']:>10.5f}  ${pnl_usd:>+9.2f}  {icon:>6s}")

    wins = sum(1 for t in all_results if t["outcome"] == "TP")
    losses = sum(1 for t in all_results if t["outcome"] == "SL")
    opens = sum(1 for t in all_results if t["outcome"] == "OPEN")
    print(f"\n  TOTAL: {len(all_results)} trades | {wins} wins | {losses} losses | {opens} open")
    if wins + losses > 0:
        print(f"  Win rate (closed): {wins}/{wins+losses} = {wins/(wins+losses)*100:.0f}%")
    print(f"  Total PnL: ${total_pnl:+,.2f} ({total_pnl/10000*100:+.2f}%)")


if __name__ == "__main__":
    main()
