"""
MT5 Connector — handles connection, data retrieval, and order execution.

Each account gets its own connector instance.
MT5 limitation: only one terminal login at a time, so we rotate accounts.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import MetaTrader5 as mt5

from algosbz.core.config import InstrumentConfig

logger = logging.getLogger(__name__)


class MT5Connector:

    def __init__(self, login: int, password: str, server: str,
                 symbol_map: dict = None):
        self.login = login
        self.password = password
        self.server = server
        self.symbol_map = symbol_map or {}
        self._connected = False

    def connect(self) -> bool:
        """Connect to MT5 terminal with this account's credentials."""
        if not mt5.initialize():
            logger.error("MT5 initialize() failed: %s", mt5.last_error())
            return False

        authorized = mt5.login(self.login, password=self.password,
                               server=self.server)
        if not authorized:
            logger.error("MT5 login failed for %d@%s: %s",
                         self.login, self.server, mt5.last_error())
            return False

        info = mt5.account_info()
        logger.info("Connected: %d@%s balance=%.2f equity=%.2f",
                     self.login, self.server, info.balance, info.equity)
        self._connected = True
        return True

    def disconnect(self):
        """Shutdown MT5 connection."""
        mt5.shutdown()
        self._connected = False

    def map_symbol(self, internal_symbol: str) -> str:
        """Map our internal symbol name to broker's MT5 symbol."""
        return self.symbol_map.get(internal_symbol, internal_symbol)

    # ─── Account info ───────────────────────────────────────────

    def get_account_info(self) -> Optional[dict]:
        """Get current account balance, equity, etc."""
        info = mt5.account_info()
        if info is None:
            return None
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "profit": info.profit,
        }

    # ─── Market data ────────────────────────────────────────────

    def get_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        """Get recent OHLCV bars for a symbol."""
        mt5_symbol = self.map_symbol(symbol)

        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
        }
        mt5_tf = tf_map.get(timeframe)
        if mt5_tf is None:
            raise ValueError(f"Unknown timeframe: {timeframe}")

        # Ensure symbol is visible in Market Watch
        if not mt5.symbol_select(mt5_symbol, True):
            logger.warning("Failed to select symbol %s", mt5_symbol)

        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning("No bars for %s %s", mt5_symbol, timeframe)
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time")
        df = df.rename(columns={
            "tick_volume": "volume",
        })
        # Keep standard columns
        cols = ["open", "high", "low", "close", "volume", "spread"]
        df = df[[c for c in cols if c in df.columns]]

        # Convert spread from points to price
        # MT5 reports spread in points (smallest price increment)
        # We need it in price units for our strategies
        if "spread" in df.columns:
            tick_info = mt5.symbol_info(mt5_symbol)
            if tick_info:
                df["spread"] = df["spread"] * tick_info.point

        return df

    # ─── Order execution ────────────────────────────────────────

    def place_market_order(self, symbol: str, direction: str,
                           volume: float, sl: float, tp: Optional[float],
                           comment: str = "") -> Optional[dict]:
        """
        Place a market order with SL/TP.

        Args:
            symbol: Internal symbol name (mapped to broker's name)
            direction: "BUY" or "SELL"
            volume: Lot size
            sl: Stop loss price
            tp: Take profit price (or None)
            comment: Order comment for tracking
        """
        mt5_symbol = self.map_symbol(symbol)

        # Get current price
        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            logger.error("No tick for %s", mt5_symbol)
            return None

        if direction == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(sl, 5),
            "deviation": 20,  # max slippage in points
            "magic": 20250323,  # magic number to identify our trades
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if tp is not None:
            request["tp"] = round(tp, 5)

        result = mt5.order_send(request)
        if result is None:
            logger.error("order_send returned None for %s", mt5_symbol)
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("Order failed: %s (code %d)", result.comment, result.retcode)
            return None

        logger.info("ORDER FILLED: %s %s %.2f lots @ %.5f SL=%.5f TP=%s [%s]",
                     direction, mt5_symbol, volume, result.price,
                     sl, tp, comment)
        return {
            "ticket": result.order,
            "price": result.price,
            "volume": result.volume,
        }

    def get_open_positions(self, magic: int = 20250323) -> list[dict]:
        """Get all open positions placed by us (filtered by magic number)."""
        positions = mt5.positions_get()
        if positions is None:
            return []

        our_positions = []
        for pos in positions:
            if pos.magic == magic:
                our_positions.append({
                    "ticket": pos.ticket,
                    "symbol": pos.symbol,
                    "direction": "BUY" if pos.type == 0 else "SELL",
                    "volume": pos.volume,
                    "open_price": pos.price_open,
                    "sl": pos.sl,
                    "tp": pos.tp,
                    "profit": pos.profit,
                    "comment": pos.comment,
                    "time": datetime.fromtimestamp(pos.time),
                })
        return our_positions

    def close_position(self, ticket: int) -> bool:
        """Close a specific position by ticket."""
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            return False
        pos = pos[0]

        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            return False

        if pos.type == 0:  # BUY → close with SELL
            price = tick.bid
            order_type = mt5.ORDER_TYPE_SELL
        else:  # SELL → close with BUY
            price = tick.ask
            order_type = mt5.ORDER_TYPE_BUY

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 20250323,
            "comment": "close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info("CLOSED ticket %d @ %.5f", ticket, result.price)
            return True
        logger.error("Close failed for ticket %d: %s", ticket,
                      result.comment if result else "None")
        return False
