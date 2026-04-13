"""
AlgoSbz Live Trader — FTMO exam factory + funded account manager.

CRITICAL: This must replicate the backtest engine EXACTLY.
Backtest flow per bar:
  1. Process SL/TP on open positions (broker checks bar highs/lows)
  2. Execute PENDING signal at THIS bar's open (from previous bar)
     - Gap adjustment: shift SL/TP by (bar_open - ref_price)
     - Anti-martingale multiplier applied
     - RiskManager.evaluate_signal for sizing + DD checks
  3. Generate signal on THIS bar (stored as pending for NEXT bar)
  4. has_position checked per combo (no duplicate entries)

Live adaptation:
  - "pending signal" = signal generated on last COMPLETED bar,
    executed when NEXT bar opens (= current forming bar's open)
  - Each account has its own RiskManager + EquityManager
  - MT5 limitation: rotate logins

Usage:
    python -X utf8 scripts/live_trader.py
    python -X utf8 scripts/live_trader.py --dry-run
"""
import sys
import time
import json
import logging
import argparse
import importlib
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
import pandas as pd

from algosbz.core.config import load_config, load_all_instruments, InstrumentConfig
from algosbz.core.enums import SignalAction, Direction
from algosbz.core.models import Signal
from algosbz.risk.manager import RiskManager
from algosbz.risk.equity_manager import EquityManager, EquityManagerConfig
from algosbz.live.runtime import utc_now

from scripts.challenge_decks_v7_expanded import ALL_COMBOS, STRAT_REGISTRY
from algosbz.live import telegram

# ─── Configuration ──────────────────────────────────────────────

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "accounts.yaml"
STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "live_state.json"
LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "live_trades.log"

POLL_INTERVAL = 30  # seconds between bar checks
HISTORY_BARS = 500  # bars of history for strategy setup

# Ensure data directory exists before setting up logging
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

from logging.handlers import RotatingFileHandler

_stream = logging.StreamHandler()
_stream.flush = lambda: _stream.stream.flush()
_file = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[_stream, _file],
    force=True,
)
logger = logging.getLogger(__name__)


def write_json_atomic(path: Path, payload: dict):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    tmp_path.replace(path)


def parse_combo_from_comment(comment: str) -> str | None:
    if not comment or not comment.startswith("AS_"):
        return None
    body = comment[3:]
    if "_" not in body:
        return None
    combo_name, _state = body.rsplit("_", 1)
    if combo_name in ALL_COMBOS:
        return combo_name
    return None


# ─── Strategy Manager ───────────────────────────────────────────

class StrategyManager:
    """
    Manages strategies and replicates backtest signal flow.

    Key backtest behaviors replicated:
    1. setup() called ONCE on initial history (not on every new bar)
    2. Signal generated on COMPLETED bar → stored as pending
    3. Pending signal executed on NEXT bar's open
    4. has_position tracked per combo
    """

    def __init__(self, deck: list[str], symbol_map: dict):
        self.deck = deck
        self.symbol_map = symbol_map
        self.strategies = {}       # combo → strategy instance
        self.timeframes = {}       # combo → "H4"/"H1"/etc
        self.symbols = {}          # combo → internal symbol
        self.bar_data = {}         # (symbol, tf) → DataFrame
        self.last_bar_time = {}    # (symbol, tf) → last processed bar timestamp
        self.pending_signals = {}  # combo → Signal (waiting for next bar)
        self.last_signal_bar = {}  # combo → timestamp of bar that generated signal
        self._setup_done = set()   # combos that have been setup

    def load_strategies(self):
        for combo_name in self.deck:
            entry = ALL_COMBOS[combo_name]
            info = STRAT_REGISTRY[entry["strat"]]
            mod = importlib.import_module(info["module"])
            cls = getattr(mod, info["class"])
            self.strategies[combo_name] = cls(entry["params"])
            self.timeframes[combo_name] = self.strategies[combo_name].required_timeframe()
            self.symbols[combo_name] = entry["symbol"]
        logger.info("Loaded %d strategies", len(self.strategies))

    def get_required_feeds(self) -> list[tuple[str, str]]:
        feeds = set()
        for combo in self.deck:
            feeds.add((self.symbols[combo], self.timeframes[combo]))
        return list(feeds)

    def setup_with_history(self, mt5_conn):
        """Download history and run setup() ONCE per strategy (like backtest)."""
        for sym, tf in self.get_required_feeds():
            logger.info("Fetching %d bars of %s %s...", HISTORY_BARS, sym, tf)
            df = mt5_conn.get_bars(sym, tf, HISTORY_BARS)
            if df.empty:
                logger.error("No data for %s %s", sym, tf)
                continue
            self.bar_data[(sym, tf)] = df
            self.last_bar_time[(sym, tf)] = df.index[-1]
            logger.info("  %s %s: %d bars, last=%s (forming)", sym, tf, len(df), df.index[-1])

        # Setup each strategy ONCE on full history (matches backtest)
        for combo in self.deck:
            key = (self.symbols[combo], self.timeframes[combo])
            if key not in self.bar_data:
                continue
            self.strategies[combo].setup(self.bar_data[key])
            self._setup_done.add(combo)
            # Mark last completed bar so we don't generate stale signals
            # from the startup batch — only truly new bars will trigger signals
            df = self.bar_data[key]
            if len(df) >= 2:
                self.last_signal_bar[combo] = df.index[-2]
            logger.info("  %s setup OK (%d bars)", combo, len(self.bar_data[key]))

    def check_new_bars(self, mt5_conn) -> dict[tuple[str, str], pd.DataFrame]:
        """
        Check for new completed bars. Returns {(sym,tf): new_bar_df} for feeds
        that have a new bar since last check.
        """
        new_feeds = {}
        for sym, tf in self.get_required_feeds():
            key = (sym, tf)
            if key not in self.bar_data:
                continue

            recent = mt5_conn.get_bars(sym, tf, 5)
            if recent.empty:
                continue

            last_new = recent.index[-1]
            last_known = self.last_bar_time.get(key)

            if last_known is not None and last_new <= last_known:
                continue

            old_df = self.bar_data[key]

            # Refresh the previously-forming bar with its final OHLC
            last_old_ts = old_df.index[-1]
            if last_old_ts in recent.index:
                old_df.loc[last_old_ts] = recent.loc[last_old_ts]

            # Append only truly new bars
            new_only = recent[recent.index > last_old_ts]
            if new_only.empty:
                continue

            self.bar_data[key] = pd.concat([old_df, new_only]).tail(HISTORY_BARS)
            self.last_bar_time[key] = self.bar_data[key].index[-1]
            new_feeds[key] = new_only

            logger.info("NEW BAR: %s %s @ %s (%d new bars)",
                        sym, tf, self.last_bar_time[key], len(new_only))

        return new_feeds

    def generate_signals(self, new_feeds: dict,
                         has_position_fn) -> dict[str, Signal]:
        """
        Generate signals on newly completed bars.

        Replicates backtest: strategy.on_bar(idx, bar, has_position)
        on ALL completed bars since last check, sequentially, to maintain
        strategy internal state (stateful strategies need every bar).

        has_position_fn(combo_name) → bool: checks if combo has open position.

        Which bars to evaluate:
        - df[-1] is the currently-forming bar from MT5 (incomplete OHLC).
          We must NOT evaluate it — its OHLC will change.
        - df[-2] and earlier are completed bars with final OHLC.
        - We evaluate bars from (last_signal_bar, df[-2]] inclusive.
        - On startup, last_signal_bar is set to df[-2] so nothing runs
          until the NEXT bar completes (matching backtest: no stale signals).

        IMPORTANT: check_new_bars guarantees that when new_feeds is non-empty,
        at least one new bar was appended. The previously-forming bar (old df[-1])
        got its OHLC refreshed (line 173) and is now df[-2]. The new forming
        bar is df[-1]. So df[-2] is the bar we need to evaluate.
        """
        signals = {}
        for combo in self.deck:
            if combo not in self._setup_done:
                continue

            key = (self.symbols[combo], self.timeframes[combo])
            if key not in new_feeds:
                continue

            df = self.bar_data[key]
            strategy = self.strategies[combo]

            # Re-setup with extended data (backtest does setup once on full data,
            # but indicators need to cover new bars too)
            strategy.setup(df)

            # Determine range of bars to evaluate:
            # - Start: after last_signal_bar (already processed)
            # - End: up to and including df[-2] (last completed bar)
            #   df[-1] is the forming bar — skip it
            last_processed = self.last_signal_bar.get(combo)

            # Find start position (after last processed bar)
            start_idx = 0
            if last_processed is not None:
                matches = df.index.get_indexer([last_processed], method=None)
                if matches[0] >= 0:
                    start_idx = matches[0] + 1
                else:
                    # last_processed was dropped by tail() — find first bar after it
                    start_idx = df.index.searchsorted(last_processed, side="right")

            # End: exclude df[-1] (forming bar)
            end_idx = len(df) - 1

            if start_idx >= end_idx:
                logger.info("SKIP %s: no completed bars to evaluate "
                            "(start=%d end=%d df_len=%d last_proc=%s forming=%s)",
                            combo, start_idx, end_idx, len(df),
                            last_processed, df.index[-1])
                continue

            for idx in range(start_idx, end_idx):
                bar = df.iloc[idx]
                bar_time = df.index[idx]

                has_pos = has_position_fn(combo)
                signal = strategy.on_bar(idx, bar, has_pos)
                self.last_signal_bar[combo] = bar_time

                logger.info("EVAL %s bar=%s → %s", combo, bar_time, signal.action.name)

                if signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
                    if not has_pos:
                        self.pending_signals[combo] = signal
                        logger.info("PENDING SIGNAL: %s → %s SL=%.5f TP=%s (bar=%s)",
                                    combo, signal.action.name,
                                    signal.stop_loss or 0,
                                    f"{signal.take_profit:.5f}" if signal.take_profit else "None",
                                    bar_time)

        return signals

    def get_executable_signals(self, mt5_conn) -> dict[str, tuple[Signal, float]]:
        """
        Get pending signals ready for execution at current bar's open.

        Returns {combo: (gap_adjusted_signal, fill_price)}.

        This is the "execute pending at next bar's open" step from backtest.
        """
        executable = {}

        for combo, signal in list(self.pending_signals.items()):
            sym = self.symbols[combo]
            tf = self.timeframes[combo]
            key = (sym, tf)

            if key not in self.bar_data:
                continue

            # Current bar's open = execution price (matches backtest)
            df = self.bar_data[key]
            current_bar = df.iloc[-1]  # the forming bar
            fill_price = current_bar["open"]

            # Gap adjustment: shift SL/TP by (fill_price - ref_price)
            # This preserves ATR-based distances despite overnight gaps
            ref_price = signal.metadata.get("ref_price")
            adjusted_signal = signal
            if ref_price is not None and signal.stop_loss is not None:
                gap = fill_price - ref_price
                adjusted_sl = signal.stop_loss + gap
                adjusted_tp = (signal.take_profit + gap) if signal.take_profit else None
                adjusted_signal = Signal(
                    action=signal.action,
                    symbol=signal.symbol,
                    timestamp=signal.timestamp,
                    stop_loss=adjusted_sl,
                    take_profit=adjusted_tp,
                    metadata=signal.metadata,
                )

            executable[combo] = (adjusted_signal, fill_price)

        # DON'T clear here — signals are cleared per-combo after successful
        # execution or explicit rejection. This prevents signal loss on
        # market-closed / MT5 disconnect / transient failures.
        return executable

    def confirm_signal_consumed(self, combo: str):
        """Mark a pending signal as consumed (executed or rejected by risk checks)."""
        self.pending_signals.pop(combo, None)

    def discard_stale_signals(self):
        """
        Remove pending signals that have missed their execution window.

        A signal generated on bar[i] should execute on bar[i+1].
        If we're now on bar[i+2] or later, the signal is stale.
        This matches backtest behavior: pending signals only live for 1 bar.
        """
        stale = []
        for combo, signal in self.pending_signals.items():
            key = (self.symbols[combo], self.timeframes[combo])
            if key in self.bar_data:
                df = self.bar_data[key]
                if len(df) >= 3:
                    signal_bar = self.last_signal_bar.get(combo)
                    # Signal from bar[i] should execute during bar[i+1].
                    # If current forming bar is bar[i+2] or later, it's stale.
                    # df.index[-1] = forming bar, df.index[-2] = last completed bar
                    # Signal is stale if it was generated before the last completed bar
                    if signal_bar is not None and signal_bar < df.index[-2]:
                        stale.append(combo)
        for combo in stale:
            logger.warning("STALE signal discarded: %s (missed execution window)", combo)
            self.pending_signals.pop(combo, None)


# ─── Live Account (with RiskManager + EquityManager) ────────────

class LiveAccount:
    """
    Wraps AccountState with RiskManager + EquityManager to match backtest.

    Each account has its own:
    - RiskManager: position sizing, DD checks, max positions
    - EquityManager: anti-martingale tiers, progressive ramp, win streak
    - Open position tracking per combo
    """

    def __init__(self, name: str, config: dict, mode_configs: dict,
                 app_config, instruments: dict):
        from algosbz.live.account_manager import AccountState
        self.state = AccountState(name, config, mode_configs)
        self.app_config = app_config
        self.instruments = instruments

        # Per-account RiskManagers (one per instrument, like backtest)
        self._risk_managers = {}  # symbol → RiskManager
        self._equity_manager = EquityManager(EquityManagerConfig())
        self._equity_manager.initialize(self.state.initial_balance)
        self._base_risk = self.state.risk_per_trade

        # Position tracking per combo (matches backtest broker.has_position)
        self.open_positions = {}  # combo → MT5 ticket (or "dry_run")

    def _count_symbol_positions(self, symbol: str) -> int:
        return sum(
            1
            for combo in self.open_positions
            if ALL_COMBOS.get(combo, {}).get("symbol") == symbol
        )

    def _get_risk_manager(self, symbol: str) -> RiskManager:
        """Get or create RiskManager for a symbol."""
        if symbol not in self._risk_managers:
            risk_cfg = deepcopy(self.app_config.risk)
            risk_cfg.risk_per_trade = self.state.risk_per_trade
            risk_cfg.daily_dd_limit = 0.045  # 4.5% safety
            risk_cfg.max_dd_limit = 0.09     # 9% safety
            rm = RiskManager(risk_cfg, self.instruments[symbol])
            rm.initialize(self.state.initial_balance)
            self._risk_managers[symbol] = rm
        rm = self._risk_managers[symbol]
        rm.current_equity = self.state.current_equity
        rm.start_of_day_equity = self.state._day_start_equity
        rm.open_position_count = self._count_symbol_positions(symbol)
        return rm

    def has_position(self, combo: str) -> bool:
        return combo in self.open_positions

    def evaluate_signal(self, combo: str, signal: Signal,
                        fill_price: float) -> dict | None:
        """
        Evaluate signal through RiskManager + EquityManager.
        Returns order dict or None if rejected.
        Matches backtest engine lines 112-144.
        """
        if len(self.open_positions) >= self.app_config.risk.max_positions:
            logger.debug("[%s] SKIP %s: account max positions reached (%d)",
                         self.state.name, combo, self.app_config.risk.max_positions)
            return None

        # Portfolio controls first
        symbol = ALL_COMBOS[combo]["symbol"]
        can, reason = self.state.can_trade(combo, symbol)
        if not can:
            logger.debug("[%s] SKIP %s: %s", self.state.name, combo, reason)
            return None

        # Already has position for this combo (matches backtest: !broker.has_position)
        if self.has_position(combo):
            logger.debug("[%s] SKIP %s: already has position", self.state.name, combo)
            return None

        # Anti-martingale multiplier (matches backtest engine line 114)
        multiplier = self._equity_manager.get_risk_multiplier()
        if multiplier <= 0 or self._equity_manager.should_stop_trading():
            logger.debug("[%s] SKIP %s: equity manager halt (mult=%.2f)",
                        self.state.name, combo, multiplier)
            return None

        # RiskManager evaluate_signal (matches backtest engine lines 134-137)
        rm = self._get_risk_manager(symbol)
        rm.current_equity = self.state.current_equity
        rm.start_of_day_equity = self.state._day_start_equity

        # Apply multiplier to risk (matches backtest: risk *= multiplier)
        base_risk = self.state.risk_per_trade
        rm.config.risk_per_trade = base_risk * multiplier
        order = rm.evaluate_signal(signal, self.state.current_equity, fill_price)
        rm.config.risk_per_trade = base_risk  # restore

        if order is None:
            logger.debug("[%s] SKIP %s: RiskManager rejected", self.state.name, combo)
            return None

        direction = "BUY" if order.direction == Direction.LONG else "SELL"

        logger.info("[%s] APPROVED: %s %s %.2f lots @ %.5f SL=%.5f TP=%s (mult=%.2f)",
                    self.state.name, direction, combo, order.volume, fill_price,
                    order.stop_loss,
                    f"{order.take_profit:.5f}" if order.take_profit else "None",
                    multiplier)

        return {
            "account": self,
            "combo": combo,
            "direction": direction,
            "symbol": self.state.mode_configs.get("symbol_map", {}).get(symbol, symbol),
            "internal_symbol": symbol,
            "volume": order.volume,
            "sl": order.stop_loss,
            "tp": order.take_profit,
            "comment": f"AS_{combo}_{self.state.state}",
            "fill_price": fill_price,
        }

    def on_trade_executed(self, combo: str, ticket):
        """After MT5 confirms fill."""
        symbol = ALL_COMBOS[combo]["symbol"]
        self.open_positions[combo] = ticket
        self.state.on_trade_opened(combo, symbol)

        rm = self._get_risk_manager(symbol)
        rm.on_trade_opened()

    def on_trade_closed(self, combo: str, pnl: float):
        """When SL/TP hit (detected by polling MT5 positions)."""
        self.drop_open_position(combo)
        self.state.on_trade_closed(combo, pnl)

        # Update EquityManager (matches backtest: eq_mgr.on_trade_closed)
        self._equity_manager.on_trade_closed(pnl, self.state.current_equity)

        # Update RiskManager tracking (position count, trading days)
        # Do NOT call rm.on_trade_closed() — it would double-count PnL in equity.
        # rm.current_equity is synced externally in evaluate_signal() before each use.
        symbol = ALL_COMBOS[combo]["symbol"]
        rm = self._get_risk_manager(symbol)
        rm.open_position_count = max(0, rm.open_position_count - 1)
        rm.daily_pnl += pnl
        rm._trading_days.add(datetime.now().date())
        rm.current_equity = self.state.current_equity
        rm.start_of_day_equity = self.state._day_start_equity
        if rm.current_equity > rm.high_water_mark:
            rm.high_water_mark = rm.current_equity

    def sync_equity(self, equity: float):
        """Sync from MT5 account info."""
        self.state.current_equity = equity

    def sync_runtime_day(self, equity: float):
        """Resume the current trading day without resetting it on restart."""
        self.state.sync_runtime_day(equity)

    def new_day(self, equity: float):
        """Daily reset for all managers."""
        self.state.new_day(equity)
        self._equity_manager.on_bar(datetime.now())
        for rm in self._risk_managers.values():
            rm.update_on_bar(datetime.now(), 0.0)

    def register_recovered_position(self, combo: str, ticket: int,
                                    opened_at: datetime | None):
        previous_ticket = self.open_positions.get(combo)
        if previous_ticket == ticket:
            return False

        self.open_positions[combo] = ticket
        symbol = ALL_COMBOS[combo]["symbol"]
        if previous_ticket is None:
            self.state.register_recovered_position(combo, symbol, opened_at)
        if symbol in self._risk_managers:
            self._risk_managers[symbol].open_position_count = self._count_symbol_positions(symbol)
        return previous_ticket is None

    def drop_open_position(self, combo: str):
        ticket = self.open_positions.pop(combo, None)
        symbol = ALL_COMBOS.get(combo, {}).get("symbol")
        if symbol and symbol in self._risk_managers:
            self._risk_managers[symbol].open_position_count = self._count_symbol_positions(symbol)
        return ticket


# ─── State Persistence ──────────────────────────────────────────

def save_state(accounts: list[LiveAccount], day_date: str,
               strat_mgr: 'StrategyManager' = None):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "day_date": day_date,
        "last_update": utc_now().isoformat(),
        "accounts": {},
    }
    for acct in accounts:
        s = acct.state
        # Save open positions with their MT5 tickets for restore
        positions = {}
        for combo, ticket in acct.open_positions.items():
            positions[combo] = ticket if isinstance(ticket, int) else str(ticket)
        state["accounts"][s.name] = {
            "state": s.state,
            "equity": s.current_equity,
            "trading_days": s.trading_days,
            "total_trades": s.total_trades,
            "total_pnl": s.total_pnl,
            "open_positions": positions,
            "runtime": s.runtime_state_payload(),
        }

    # Persist pending signals (so they survive restart)
    if strat_mgr and strat_mgr.pending_signals:
        pending = {}
        for combo, signal in strat_mgr.pending_signals.items():
            pending[combo] = {
                "action": signal.action.name,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "metadata": signal.metadata,
                "generated_at": signal.timestamp.isoformat(),
                "timeframe": strat_mgr.timeframes.get(combo, "H4"),
            }
        state["pending_signals"] = pending

    write_json_atomic(STATE_PATH, state)


def load_state(accounts: list[LiveAccount]) -> dict:
    """
    Restore state from previous session. Returns pending_signals dict (raw).
    Must be called AFTER accounts are created but BEFORE main loop.
    """
    if not STATE_PATH.exists():
        return {}

    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning("Could not load state file, starting fresh")
        return {}

    logger.info("Restoring state from %s", state.get("last_update", "unknown"))

    for acct in accounts:
        name = acct.state.name
        if name not in state.get("accounts", {}):
            continue
        saved = state["accounts"][name]

        # Restore open positions (will be validated against MT5 on first sync)
        if "open_positions" in saved and isinstance(saved["open_positions"], dict):
            for combo, ticket in saved["open_positions"].items():
                if isinstance(ticket, int) or (isinstance(ticket, str) and ticket.isdigit()):
                    acct.open_positions[combo] = int(ticket)
                elif ticket == "dry_run":
                    acct.open_positions[combo] = "dry_run"
            logger.info("[%s] Restored %d open positions: %s",
                        name, len(acct.open_positions),
                        list(acct.open_positions.keys()))

        # Restore cumulative counters
        acct.state.current_equity = saved.get("equity", acct.state.current_equity)
        acct.state.trading_days = saved.get("trading_days", 0)
        acct.state.total_trades = saved.get("total_trades", 0)
        acct.state.total_pnl = saved.get("total_pnl", 0.0)
        acct.state.state = saved.get("state", acct.state.state)
        acct.state.restore_runtime_state(saved.get("runtime", {}))

    return state.get("pending_signals", {})


def save_trade_log(trade_info: dict):
    log_file = LOG_PATH.parent / "trade_history.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    event_time = trade_info.get("ts") or utc_now().isoformat()
    payload = {
        **trade_info,
        "ts": event_time,
        "logged_at": utc_now().isoformat(),
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


# ─── Position Sync ──────────────────────────────────────────────

def get_deal_pnl(ticket: int) -> float:
    """
    Query MT5 deal history for the PnL of a closed position.
    Must be called while logged into the correct account.
    """
    import MetaTrader5 as mt5
    from datetime import timezone

    # Search deals in last 60 days for this position (covers long offline periods)
    now = datetime.now(timezone.utc)
    deals = mt5.history_deals_get(now - timedelta(days=60), now)
    if deals is None:
        return 0.0

    total_pnl = 0.0
    for deal in deals:
        if deal.position_id == ticket and deal.entry == 1:  # entry=1 means exit deal
            total_pnl += deal.profit + deal.commission + deal.swap
    return total_pnl


def sync_closed_positions_for_account(acct: LiveAccount, mt5_conn) -> list[str]:
    """
    Check which positions have been closed for ONE account.
    Must be logged into this account's MT5 session.
    Returns list of closed combo names.
    """
    if not acct.open_positions:
        return []

    current_mt5_positions = mt5_conn.get_open_positions()
    current_tickets = {p["ticket"] for p in current_mt5_positions}

    closed_combos = []
    for combo, ticket in list(acct.open_positions.items()):
        if ticket == "dry_run":
            continue
        if ticket not in current_tickets:
            # Position closed — get real PnL from deal history
            pnl = get_deal_pnl(ticket)
            acct.on_trade_closed(combo, pnl)
            closed_combos.append(combo)
            logger.info("[%s] Position closed: %s ticket=%d PnL=%.2f",
                        acct.state.name, combo, ticket, pnl)
            telegram.notify_trade_closed(acct.state.name, combo, pnl,
                                         ticket, acct.state.current_equity)

            save_trade_log({
                "event": "CLOSE",
                "account": acct.state.name,
                "combo": combo,
                "direction": "CLOSE",
                "pnl": pnl,
                "ticket": ticket,
                "state": acct.state.state,
            })

    return closed_combos


# ─── Main Loop ──────────────────────────────────────────────────

def reconcile_account_positions(acct: LiveAccount, mt5_conn) -> dict[str, int]:
    """
    Make MT5 the source of truth for open positions after restart/disconnect.
    """
    mt5_positions = mt5_conn.get_open_positions()
    live_tickets = {p["ticket"] for p in mt5_positions}
    recovered = 0
    removed = 0

    for combo, ticket in list(acct.open_positions.items()):
        if ticket == "dry_run":
            continue
        if ticket not in live_tickets:
            acct.drop_open_position(combo)
            removed += 1
            logger.warning("[%s] Removed stale local position: %s ticket=%s",
                           acct.state.name, combo, ticket)

    for pos in mt5_positions:
        combo = parse_combo_from_comment(pos.get("comment", ""))
        if combo is None:
            continue
        if acct.register_recovered_position(combo, pos["ticket"], pos.get("time")):
            recovered += 1
            logger.warning("[%s] Recovered MT5 position: %s ticket=%d",
                           acct.state.name, combo, pos["ticket"])
            save_trade_log({
                "event": "OPEN_RECOVERED",
                "account": acct.state.name,
                "combo": combo,
                "direction": pos["direction"],
                "symbol": pos["symbol"],
                "volume": pos["volume"],
                "sl": pos["sl"],
                "tp": pos["tp"],
                "fill_price": pos["open_price"],
                "ticket": pos["ticket"],
                "state": acct.state.state,
                "ts": pos["time"].isoformat() if pos.get("time") else utc_now().isoformat(),
            })

    return {"recovered": recovered, "removed": removed}


def main():
    parser = argparse.ArgumentParser(description="AlgoSbz Live Trader")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate signals but don't place real orders")
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle and exit (for testing)")
    args = parser.parse_args()

    # Load configs
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    symbol_map = cfg.get("symbol_map", {})
    deck = cfg["deck"]
    app_config = load_config()
    instruments = load_all_instruments()

    logger.info("=" * 80)
    logger.info("  AlgoSbz Live Trader %s", "DRY RUN" if args.dry_run else "LIVE")
    logger.info("  Deck: %d combos", len(deck))
    logger.info("  Backtest-faithful: pending signals, gap adjustment,")
    logger.info("    anti-martingale, RiskManager sizing, has_position tracking")
    logger.info("=" * 80)

    # Initialize strategies
    strat_mgr = StrategyManager(deck, symbol_map)
    strat_mgr.load_strategies()

    # Initialize accounts with RiskManager + EquityManager
    mode_configs = {
        "exam_mode": cfg["exam_mode"],
        "funded_mode": cfg["funded_mode"],
        "symbol_map": symbol_map,
        "runtime": {
            "daily_reset_hour": app_config.risk.daily_reset_hour,
            "daily_reset_timezone": app_config.risk.daily_reset_timezone,
        },
    }
    accounts: list[LiveAccount] = []
    for acct_cfg in cfg["accounts"]:
        if acct_cfg.get("enabled", True) and acct_cfg["login"] != 0:
            accounts.append(LiveAccount(
                acct_cfg["name"], acct_cfg, mode_configs,
                app_config, instruments,
            ))

    if not accounts:
        logger.error("No enabled accounts. Fill in config/accounts.yaml")
        return

    logger.info("Accounts: %d", len(accounts))

    # Restore state from previous session (open positions, counters)
    saved_pending = load_state(accounts)

    # Pending signals are NOT restored here. They will be validated after
    # history download, when we know the current bar timestamps.
    # This prevents executing stale signals from hours/days ago.
    _saved_pending_raw = saved_pending

    # ─── Initial setup ───────────────────────────────────────────
    from algosbz.live.mt5_connector import MT5Connector

    # Connect to first account for data download
    first = accounts[0]
    conn = MT5Connector(first.state.login, first.state.password,
                        first.state.server, symbol_map)

    logger.info("Connecting to %s for initial data...", first.state.name)
    if not conn.connect():
        logger.error("MT5 connection failed. Is the terminal running?")
        return

    # Download history and setup strategies (setup called ONCE)
    strat_mgr.setup_with_history(conn)

    # Restore pending signals ONLY if they're from the immediately previous bar
    # (exactly like backtest: signal on bar[i] → execute on bar[i+1] open, never later)
    if _saved_pending_raw:
        for combo, sig_data in _saved_pending_raw.items():
            if combo not in strat_mgr.deck:
                continue
            sym = strat_mgr.symbols[combo]
            tf = strat_mgr.timeframes[combo]
            key = (sym, tf)
            if key not in strat_mgr.bar_data:
                continue

            df = strat_mgr.bar_data[key]
            if len(df) < 2:
                continue

            # Signal is valid only if generated_at falls within the previous bar's window
            generated_at = sig_data.get("generated_at")
            if not generated_at:
                logger.warning("DISCARD %s: no generated_at timestamp", combo)
                continue

            try:
                gen_time = datetime.fromisoformat(generated_at)
            except (ValueError, TypeError):
                logger.warning("DISCARD %s: bad generated_at", combo)
                continue

            prev_bar_time = df.index[-2]
            curr_bar_time = df.index[-1]
            # Signal must have been generated between prev_bar open and current bar open
            if gen_time < prev_bar_time or gen_time >= curr_bar_time:
                logger.warning("STALE signal discarded: %s (generated %s, prev bar %s, current bar %s)",
                               combo, gen_time, prev_bar_time, curr_bar_time)
                continue

            action = SignalAction[sig_data["action"]]
            symbol = ALL_COMBOS[combo]["symbol"]
            restored_signal = Signal(
                action=action, symbol=symbol, timestamp=gen_time,
                stop_loss=sig_data.get("stop_loss"),
                take_profit=sig_data.get("take_profit"),
                metadata=sig_data.get("metadata", {}),
            )
            strat_mgr.pending_signals[combo] = restored_signal
            logger.info("Restored pending signal: %s → %s (generated %s)", combo, action.name, gen_time)

    # Sync equity for first account
    info = conn.get_account_info()
    if info:
        first.sync_runtime_day(info["equity"])
        sync_closed_positions_for_account(first, conn)
        reconcile_account_positions(first, conn)
        logger.info("[%s] Equity: %.2f", first.state.name, info["equity"])
    conn.disconnect()

    # Sync remaining accounts
    for acct in accounts[1:]:
        conn = MT5Connector(acct.state.login, acct.state.password,
                           acct.state.server, symbol_map)
        if conn.connect():
            info = conn.get_account_info()
            if info:
                acct.sync_runtime_day(info["equity"])
                sync_closed_positions_for_account(acct, conn)
                reconcile_account_positions(acct, conn)
                logger.info("[%s] Equity: %.2f", acct.state.name, info["equity"])
            conn.disconnect()

    save_state(accounts, str(first.state.current_trading_day()), strat_mgr)

    for acct in accounts:
        logger.info("  %s", acct.state.status_line())

    # ─── Main trading loop ───────────────────────────────────────
    logger.info("\nTrading loop started (poll=%ds)\n", POLL_INTERVAL)

    telegram.notify_startup(len(accounts), len(deck),
                            "DRY RUN" if args.dry_run else "LIVE")

    cycle = 0
    last_day = first.state.current_trading_day()
    last_heartbeat = datetime.now()

    while True:
        cycle += 1
        try:
            # Daily reset — sync equity from MT5 FIRST for all accounts
            today = first.state.current_trading_day()
            if today != last_day:
                last_day = today
                logger.info("=== NEW DAY: %s ===", today)
                if not args.dry_run:
                    for acct in accounts:
                        conn = MT5Connector(acct.state.login, acct.state.password,
                                           acct.state.server, symbol_map)
                        if conn.connect():
                            info = conn.get_account_info()
                            if info:
                                acct.new_day(info["equity"])
                                logger.info("[%s] New day equity synced: %.2f",
                                            acct.state.name, info["equity"])
                            conn.disconnect()
                        else:
                            acct.new_day(acct.state.current_equity)
                else:
                    for acct in accounts:
                        acct.new_day(acct.state.current_equity)

            # Micro-op: if target reached but < 4 trading days, open/close
            # 0.01 EURUSD to register the trading day (no real risk)
            if not args.dry_run:
                for acct in accounts:
                    if acct.state.target_reached:
                        micro_symbol = "EURUSD"
                        conn = MT5Connector(acct.state.login, acct.state.password,
                                           acct.state.server, symbol_map)
                        if conn.connect():
                            logger.info("[%s] TARGET REACHED — executing micro-op "
                                       "(0.01 %s) for trading day %d/4",
                                       acct.state.name, micro_symbol,
                                       acct.state.trading_days + 1)
                            result = conn.place_market_order(
                                micro_symbol, "BUY", 0.01,
                                sl=0.0, tp=None,
                                comment="micro-op-min-days")
                            if result:
                                import time as _t
                                _t.sleep(2)  # brief pause to register
                                conn.close_position(result["ticket"])
                                acct.state._instr_day_trades[micro_symbol] += 1
                                logger.info("[%s] Micro-op done (ticket %d)",
                                           acct.state.name, result["ticket"])
                                telegram.send(
                                    f"\U0001f4cd <b>MICRO-OP</b> {acct.state.name}\n"
                                    f"Trading day {acct.state.trading_days + 1}/4 "
                                    f"(target already reached)")
                            else:
                                logger.warning("[%s] Micro-op failed", acct.state.name)
                            conn.disconnect()

            # Heartbeat every 4 hours
            if (datetime.now() - last_heartbeat).total_seconds() >= 4 * 3600:
                last_heartbeat = datetime.now()
                telegram.notify_heartbeat([{
                    "name": a.state.name, "state": a.state.state,
                    "equity": a.state.current_equity,
                    "initial": a.state.initial_balance,
                    "trades": a.state.total_trades,
                    "open": len(a.open_positions),
                } for a in accounts])

            # Step 1: Sync closed positions per account (rotate logins)
            # Each account needs its own MT5 session to see its positions
            if not args.dry_run:
                for acct in accounts:
                    conn = MT5Connector(acct.state.login, acct.state.password,
                                       acct.state.server, symbol_map)
                    if conn.connect():
                        sync_closed_positions_for_account(acct, conn)
                        reconcile_account_positions(acct, conn)
                        # Also sync equity while connected
                        info = conn.get_account_info()
                        if info:
                            acct.sync_runtime_day(info["equity"])
                        conn.disconnect()

            # Step 1b: DD safety check — close all if limits breached
            # Replicates backtest engine's risk_mgr.is_halted → close_all_positions
            if not args.dry_run:
                for acct in accounts:
                    if not acct.open_positions:
                        continue
                    equity = acct.state.current_equity
                    initial = acct.state.initial_balance
                    day_start = acct.state._day_start_equity

                    # Overall DD check (same formula as manager.py:190)
                    overall_dd = (initial - equity) / initial if initial > 0 else 0
                    # Daily DD check (same formula as manager.py:201)
                    daily_dd = (day_start - equity) / initial if initial > 0 else 0

                    dd_breach = False
                    if overall_dd >= 0.085:  # 8.5% total DD limit
                        logger.warning("[%s] TOTAL DD BREACH: %.2f%% — closing all positions!",
                                       acct.state.name, overall_dd * 100)
                        telegram.notify_dd_breach(acct.state.name, "TOTAL", overall_dd * 100)
                        dd_breach = True
                    elif daily_dd >= 0.04:  # 4% daily DD limit
                        logger.warning("[%s] DAILY DD BREACH: %.2f%% — closing all positions!",
                                       acct.state.name, daily_dd * 100)
                        telegram.notify_dd_breach(acct.state.name, "DAILY", daily_dd * 100)
                        dd_breach = True

                    if dd_breach:
                        conn = MT5Connector(acct.state.login, acct.state.password,
                                           acct.state.server, symbol_map)
                        if conn.connect():
                            for combo, ticket in list(acct.open_positions.items()):
                                if ticket != "dry_run":
                                    if conn.close_position(ticket):
                                        pnl = get_deal_pnl(ticket)
                                        acct.on_trade_closed(combo, pnl)
                                        save_trade_log({
                                            "event": "CLOSE",
                                            "account": acct.state.name,
                                            "combo": combo,
                                            "direction": "CLOSE",
                                            "pnl": pnl,
                                            "ticket": ticket,
                                            "state": acct.state.state,
                                        })
                                        logger.info("[%s] DD SAFETY CLOSE: %s ticket=%d PnL=%.2f",
                                                    acct.state.name, combo, ticket, pnl)
                            conn.disconnect()
                        # Clear pending signals — don't open new trades
                        strat_mgr.pending_signals.clear()
                        save_state(accounts, str(today), strat_mgr)

            # Connect to first account for market data
            conn = MT5Connector(accounts[0].state.login,
                               accounts[0].state.password,
                               accounts[0].state.server, symbol_map)

            if not conn.connect():
                logger.warning("Connection failed, retry in %ds", POLL_INTERVAL)
                time.sleep(POLL_INTERVAL)
                continue

            # Step 2: Check for new bars
            new_feeds = strat_mgr.check_new_bars(conn)

            if not new_feeds:
                conn.disconnect()
                if cycle % 20 == 0:
                    logger.info("Cycle %d — no new bars", cycle)
                if args.once:
                    break
                time.sleep(POLL_INTERVAL)
                continue

            # Step 3: Get executable pending signals (from PREVIOUS cycle)
            # This is the "execute pending at next bar open" from backtest
            executable = strat_mgr.get_executable_signals(conn)

            # Step 4: Generate NEW signals on completed bars (stored as pending)
            # These will execute on the NEXT new bar (next cycle)
            # NOTE: has_position=False here so signals are always generated.
            # Per-account position check happens in evaluate_signal() (Step 5).
            # This is correct for multi-account: account1 may have a position
            # but account2 should still be able to take the signal.
            strat_mgr.generate_signals(new_feeds, lambda combo: False)

            conn.disconnect()

            # Save state after generating signals (so pending signals survive crashes)
            save_state(accounts, str(today), strat_mgr)

            # Step 5: Execute pending signals that are now ready
            if not executable:
                if args.once:
                    break
                time.sleep(POLL_INTERVAL)
                continue

            logger.info("Cycle %d — %d executable signals: %s",
                        cycle, len(executable), list(executable.keys()))

            # Evaluate each signal against each account
            exec_queue = []
            rejected_combos = set()
            for combo, (signal, fill_price) in executable.items():
                combo_has_order = False
                for acct in accounts:
                    order = acct.evaluate_signal(combo, signal, fill_price)
                    if order:
                        exec_queue.append(order)
                        combo_has_order = True
                if not combo_has_order:
                    # All accounts rejected → signal consumed (risk checks, not failure)
                    rejected_combos.add(combo)

            # Mark rejected signals as consumed (they won't retry)
            for combo in rejected_combos:
                strat_mgr.confirm_signal_consumed(combo)

            if not exec_queue:
                logger.info("No orders passed all checks.")
                # Clean up stale signals
                strat_mgr.discard_stale_signals()
                if args.once:
                    break
                time.sleep(POLL_INTERVAL)
                continue

            # Step 6: Execute via MT5
            executed_combos = set()
            if args.dry_run:
                for order in exec_queue:
                    acct = order["account"]
                    logger.info("[DRY RUN] %s %s %s %.2f lots SL=%.5f TP=%s",
                                acct.state.name, order["direction"],
                                order["combo"], order["volume"],
                                order["sl"],
                                f"{order['tp']:.5f}" if order["tp"] else "None")
                    acct.on_trade_executed(order["combo"], "dry_run")
                    executed_combos.add(order["combo"])
                    telegram.notify_trade_opened(
                        acct.state.name, order["direction"], order["combo"],
                        order["volume"], order["fill_price"],
                        order["sl"], order["tp"], acct.state.state)
            else:
                # Group by account, execute with rotation
                by_account = defaultdict(list)
                for order in exec_queue:
                    by_account[order["account"].state.name].append(order)

                for acct_name, orders in by_account.items():
                    acct = next(a for a in accounts if a.state.name == acct_name)
                    conn = MT5Connector(acct.state.login, acct.state.password,
                                       acct.state.server, symbol_map)

                    if not conn.connect():
                        logger.error("[%s] Connection failed for execution", acct_name)
                        continue

                    for order in orders:
                        result = conn.place_market_order(
                            symbol=order["internal_symbol"],
                            direction=order["direction"],
                            volume=order["volume"],
                            sl=order["sl"],
                            tp=order["tp"],
                            comment=order["comment"],
                        )

                        if result:
                            save_trade_log({
                                "event": "OPEN",
                                "account": acct.state.name,
                                "combo": order["combo"],
                                "direction": order["direction"],
                                "symbol": order["symbol"],
                                "volume": result["volume"],
                                "sl": order["sl"],
                                "tp": order["tp"],
                                "fill_price": result["price"],
                                "signal_fill_price": order["fill_price"],
                                "ticket": result["ticket"],
                                "state": acct.state.state,
                            })
                            acct.on_trade_executed(order["combo"], result["ticket"])
                            save_state(accounts, str(today), strat_mgr)
                            executed_combos.add(order["combo"])
                            telegram.notify_trade_opened(
                                account=acct.state.name,
                                direction=order["direction"],
                                combo=order["combo"],
                                volume=result["volume"],
                                fill_price=result["price"],
                                sl=order["sl"],
                                tp=order["tp"],
                                state=acct.state.state,
                            )
                        else:
                            # Execution failed — keep signal pending for retry.
                            # In backtest execution never fails, so consuming the
                            # signal on a transient error (no tick, disconnect)
                            # creates divergence: the trade is lost AND the next
                            # bar may generate a duplicate signal that wouldn't
                            # exist in backtest (because has_position would be True).
                            logger.warning("[%s] Execution failed for %s — "
                                          "signal kept pending for retry",
                                          acct_name, order["combo"])
                            telegram.send(
                                f"\u26a0\ufe0f <b>EXEC FAIL</b> {acct.state.name}\n"
                                f"{order['combo']} {order['direction']} — will retry next cycle"
                            )

                    # Sync equity after execution
                    info = conn.get_account_info()
                    if info:
                        acct.sync_equity(info["equity"])

                    conn.disconnect()

            # Confirm executed signals as consumed (failed ones stay pending for retry)
            for combo in executed_combos:
                strat_mgr.confirm_signal_consumed(combo)
            # Clean up stale signals (missed their execution window)
            strat_mgr.discard_stale_signals()

            # Save state + check transitions
            save_state(accounts, str(today), strat_mgr)

            for acct in accounts:
                transition = acct.state.check_phase_transition()
                if transition:
                    logger.info(">>> [%s] PHASE TRANSITION → %s <<<",
                                acct.state.name, transition)
                    from algosbz.live.account_manager import save_account_states
                    save_account_states(str(CONFIG_PATH),
                                       [a.state for a in accounts])
                    # Reset risk/equity managers for new phase
                    acct._equity_manager.initialize(acct.state.initial_balance)
                    acct._risk_managers.clear()
                    acct._base_risk = acct.state.risk_per_trade

            for acct in accounts:
                logger.info("  %s", acct.state.status_line())

        except KeyboardInterrupt:
            logger.info("\nShutting down...")
            save_state(accounts, str(datetime.now().date()), strat_mgr)
            break
        except Exception as e:
            logger.error("Error: %s", e, exc_info=True)
            time.sleep(POLL_INTERVAL)

        if args.once:
            break
        time.sleep(POLL_INTERVAL)

    logger.info("Live trader stopped.")


if __name__ == "__main__":
    main()
