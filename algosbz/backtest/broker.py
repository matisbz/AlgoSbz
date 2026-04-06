import logging
from typing import Optional

import pandas as pd

from algosbz.core.enums import Direction, ExitReason, OrderType
from algosbz.core.models import Fill, Order, Position, Trade
from algosbz.core.config import InstrumentConfig

logger = logging.getLogger(__name__)


class SimulatedBroker:

    def __init__(
        self,
        instrument: InstrumentConfig,
        spread_mode: str = "data",
        slippage_pips: float = 0.5,
        pessimistic_fills: bool = True,
        commission_per_lot: float = 7.0,
    ):
        self.instrument = instrument
        self.spread_mode = spread_mode
        self.slippage_pips = slippage_pips
        self.pessimistic_fills = pessimistic_fills
        self.commission_per_lot = commission_per_lot

        self._positions: dict[int, Position] = {}
        self._pending_orders: list[Order] = []
        self._trades: list[Trade] = []
        self._next_trade_id = 1

    @property
    def has_position(self) -> bool:
        return len(self._positions) > 0

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    def submit_order(self, order: Order, bar: pd.Series = None) -> Optional[Fill]:
        if order.order_type == OrderType.MARKET:
            return self._fill_market(order, bar)
        elif order.order_type == OrderType.STOP:
            self._pending_orders.append(order)
            return None
        return None

    def process_bar(self, bar: pd.Series) -> list[Trade]:
        closed_trades: list[Trade] = []

        # 1. Check pending stop orders
        filled_orders = []
        remaining = []
        for order in self._pending_orders:
            fill = self._check_stop_order(order, bar)
            if fill:
                filled_orders.append((order, fill))
            else:
                remaining.append(order)
        self._pending_orders = remaining

        # 2. Check SL/TP on open positions
        positions_to_close = []
        for pos_id, pos in list(self._positions.items()):
            result = self._check_sl_tp(pos, bar)
            if result:
                positions_to_close.append((pos_id, result))

        for pos_id, (exit_price, reason) in positions_to_close:
            trade = self._close_position(pos_id, exit_price, bar.name, reason)
            closed_trades.append(trade)

        return closed_trades

    def close_all_positions(self, price: float, timestamp, reason: ExitReason,
                            bar: pd.Series = None) -> list[Trade]:
        spread = self._get_spread(bar) if bar is not None else self.instrument.default_spread_pips * self.instrument.pip_size
        slippage = self._get_slippage()
        closed = []
        for pos_id in list(self._positions.keys()):
            pos = self._positions[pos_id]
            # Apply spread + slippage to exit price
            if pos.direction == Direction.LONG:
                exit_price = price - spread / 2 - slippage  # sell at BID - slippage
            else:
                exit_price = price + spread / 2 + slippage  # buy at ASK + slippage
            trade = self._close_position(pos_id, exit_price, timestamp, reason)
            closed.append(trade)
        return closed

    def cancel_pending_orders(self):
        self._pending_orders.clear()

    def _get_spread(self, bar: pd.Series) -> float:
        if self.spread_mode == "data" and "spread" in bar.index and bar["spread"] > 0:
            return bar["spread"]
        return self.instrument.default_spread_pips * self.instrument.pip_size

    def _get_slippage(self) -> float:
        return self.slippage_pips * self.instrument.pip_size

    def _fill_market(self, order: Order, bar: pd.Series = None) -> Fill:
        spread = self._get_spread(bar) if bar is not None else self._get_spread_from_price(order.price)
        slippage = self._get_slippage()
        commission = self.commission_per_lot * order.volume

        # Entry fill: BUY at ASK (price + spread/2), SELL at BID (price - spread/2)
        # Plus adverse slippage in both directions
        if order.direction == Direction.LONG:
            fill_price = order.price + spread / 2 + slippage
        else:
            fill_price = order.price - spread / 2 - slippage

        fill = Fill(
            order_id=order.id,
            fill_price=fill_price,
            spread_cost=spread * order.volume / self.instrument.pip_size * self.instrument.pip_value_per_lot,
            slippage=slippage,
            commission=commission,
            timestamp=order.timestamp,
        )

        pos = Position(
            id=order.id,
            symbol=order.symbol,
            direction=order.direction,
            entry_price=fill_price,
            volume=order.volume,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            entry_time=order.timestamp,
        )
        self._positions[pos.id] = pos
        return fill

    def _get_spread_from_price(self, price: float) -> float:
        return self.instrument.default_spread_pips * self.instrument.pip_size

    def _check_stop_order(self, order: Order, bar: pd.Series) -> Optional[Fill]:
        triggered = False
        if order.direction == Direction.LONG and bar["high"] >= order.price:
            triggered = True
        elif order.direction == Direction.SHORT and bar["low"] <= order.price:
            triggered = True

        if not triggered:
            return None

        spread = self._get_spread(bar)
        slippage = self._get_slippage()
        commission = self.commission_per_lot * order.volume

        # Entry fill: BUY at ASK, SELL at BID (symmetric spread)
        if order.direction == Direction.LONG:
            fill_price = order.price + spread / 2 + slippage
        else:
            fill_price = order.price - spread / 2 - slippage

        fill = Fill(
            order_id=order.id,
            fill_price=fill_price,
            spread_cost=spread * order.volume / self.instrument.pip_size * self.instrument.pip_value_per_lot,
            slippage=slippage,
            commission=commission,
            timestamp=bar.name,
        )

        pos = Position(
            id=order.id,
            symbol=order.symbol,
            direction=order.direction,
            entry_price=fill_price,
            volume=order.volume,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            entry_time=bar.name,
        )
        self._positions[pos.id] = pos
        return fill

    def _check_sl_tp(
        self, pos: Position, bar: pd.Series
    ) -> Optional[tuple[float, ExitReason]]:
        is_long = pos.direction == Direction.LONG
        spread = self._get_spread(bar)
        slippage = self._get_slippage()

        sl_hit = False
        tp_hit = False

        if is_long:
            # Long exit = sell at BID = price - spread/2
            # SL hit when BID drops to SL level → low - spread/2 <= SL
            sl_hit = (bar["low"] - spread / 2) <= pos.stop_loss
            tp_hit = pos.take_profit is not None and (bar["high"] - spread / 2) >= pos.take_profit
        else:
            # Short exit = buy at ASK = price + spread/2
            # SL hit when ASK rises to SL level → high + spread/2 >= SL
            sl_hit = (bar["high"] + spread / 2) >= pos.stop_loss
            tp_hit = pos.take_profit is not None and (bar["low"] + spread / 2) <= pos.take_profit

        if sl_hit and tp_hit:
            bar_open = bar["open"]
            if self.pessimistic_fills:
                if is_long:
                    gap_price = bar_open - spread / 2
                    exit_price = min(gap_price, pos.stop_loss) - slippage
                else:
                    gap_price = bar_open + spread / 2
                    exit_price = max(gap_price, pos.stop_loss) + slippage
                return (exit_price, ExitReason.STOP_LOSS)
            else:
                if is_long:
                    gap_price = bar_open - spread / 2
                    exit_price = max(gap_price, pos.take_profit) - slippage
                else:
                    gap_price = bar_open + spread / 2
                    exit_price = min(gap_price, pos.take_profit) + slippage
                return (exit_price, ExitReason.TAKE_PROFIT)

        if sl_hit:
            # Gap check: if bar opens beyond SL, fill at open (worse) not SL
            bar_open = bar["open"]
            if is_long:
                gap_price = bar_open - spread / 2  # BID at open
                exit_price = min(gap_price, pos.stop_loss) - slippage
            else:
                gap_price = bar_open + spread / 2  # ASK at open
                exit_price = max(gap_price, pos.stop_loss) + slippage
            return (exit_price, ExitReason.STOP_LOSS)
        if tp_hit:
            # Gap check: if bar opens beyond TP, fill at open (better)
            bar_open = bar["open"]
            if is_long:
                gap_price = bar_open - spread / 2
                exit_price = max(gap_price, pos.take_profit) - slippage
            else:
                gap_price = bar_open + spread / 2
                exit_price = min(gap_price, pos.take_profit) + slippage
            return (exit_price, ExitReason.TAKE_PROFIT)

        return None

    def _close_position(
        self, pos_id: int, exit_price: float, timestamp, reason: ExitReason
    ) -> Trade:
        pos = self._positions.pop(pos_id)

        if pos.direction == Direction.LONG:
            pnl_pips = (exit_price - pos.entry_price) / self.instrument.pip_size
        else:
            pnl_pips = (pos.entry_price - exit_price) / self.instrument.pip_size

        pnl = pnl_pips * self.instrument.pip_value_per_lot * pos.volume
        commission = self.commission_per_lot * pos.volume
        pnl -= commission

        # Risk/reward calculation
        if pos.direction == Direction.LONG:
            sl_distance = pos.entry_price - pos.stop_loss
            actual_move = exit_price - pos.entry_price
        else:
            sl_distance = pos.stop_loss - pos.entry_price
            actual_move = pos.entry_price - exit_price

        risk_reward = actual_move / sl_distance if sl_distance > 0 else 0.0

        trade = Trade(
            id=self._next_trade_id,
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            volume=pos.volume,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            pnl=pnl,
            pnl_pips=pnl_pips,
            risk_reward=risk_reward,
            stop_loss=pos.stop_loss,
            take_profit=pos.take_profit,
            exit_reason=reason,
            commission=commission,
        )
        self._next_trade_id += 1
        self._trades.append(trade)
        return trade

    def update_unrealized_pnl(self, bar: pd.Series):
        for pos in self._positions.values():
            if pos.direction == Direction.LONG:
                pos.unrealized_pnl = (
                    (bar["close"] - pos.entry_price)
                    / self.instrument.pip_size
                    * self.instrument.pip_value_per_lot
                    * pos.volume
                )
            else:
                pos.unrealized_pnl = (
                    (pos.entry_price - bar["close"])
                    / self.instrument.pip_size
                    * self.instrument.pip_value_per_lot
                    * pos.volume
                )
