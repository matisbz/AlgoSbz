import logging
from datetime import datetime
from typing import Optional

from algosbz.core.enums import Direction, OrderType, SignalAction
from algosbz.core.models import Order, Signal
from algosbz.core.config import InstrumentConfig, RiskConfig

logger = logging.getLogger(__name__)


class RiskManager:

    def __init__(self, config: RiskConfig, instrument: InstrumentConfig):
        self.config = config
        self.instrument = instrument

        self.initial_balance: float = 0.0
        self.current_equity: float = 0.0
        self.start_of_day_equity: float = 0.0
        self.high_water_mark: float = 0.0
        self.daily_pnl: float = 0.0
        self.open_position_count: int = 0
        self._halted: bool = False
        self._halt_reason: str = ""
        self._daily_halted: bool = False  # resets each day
        self._permanently_halted: bool = False  # total DD blown
        self._last_reset_date: Optional[datetime] = None
        self._next_order_id: int = 1
        self._trading_days: set = set()

    def initialize(self, initial_balance: float):
        self.initial_balance = initial_balance
        self.current_equity = initial_balance
        self.start_of_day_equity = initial_balance
        self.high_water_mark = initial_balance

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def trading_days_count(self) -> int:
        return len(self._trading_days)

    def evaluate_signal(
        self, signal: Signal, current_equity: float, current_price: float
    ) -> Optional[Order]:
        if self._halted:
            return None

        if signal.action == SignalAction.NO_ACTION:
            return None

        if signal.action == SignalAction.EXIT:
            # Exit signals are handled by the engine directly
            return None

        if self.open_position_count >= self.config.max_positions:
            logger.debug("Max positions reached (%d), rejecting signal", self.config.max_positions)
            return None

        if signal.stop_loss is None:
            logger.debug("Signal rejected: no stop loss defined")
            return None

        # Determine direction
        if signal.action == SignalAction.ENTER_LONG:
            direction = Direction.LONG
            sl_distance = abs(current_price - signal.stop_loss)
        else:
            direction = Direction.SHORT
            sl_distance = abs(signal.stop_loss - current_price)

        if sl_distance <= 0:
            logger.debug("Signal rejected: zero SL distance")
            return None

        # Check minimum risk/reward
        if signal.take_profit is not None:
            if direction == Direction.LONG:
                tp_distance = signal.take_profit - current_price
            else:
                tp_distance = current_price - signal.take_profit

            if tp_distance <= 0:
                logger.debug("Signal rejected: TP behind current price")
                return None

            rr_ratio = tp_distance / sl_distance
            if rr_ratio < self.config.min_risk_reward:
                logger.debug("Signal rejected: RR %.2f < min %.2f", rr_ratio, self.config.min_risk_reward)
                return None

        # Position sizing
        risk_amount = current_equity * self.config.risk_per_trade
        sl_pips = sl_distance / self.instrument.pip_size
        pip_value = self.instrument.pip_value_per_lot

        if sl_pips <= 0 or pip_value <= 0:
            return None

        lot_size = risk_amount / (sl_pips * pip_value)
        lot_size = max(self.instrument.min_lot, min(lot_size, self.instrument.max_lot))

        # Round to 2 decimals (standard lot precision)
        lot_size = round(lot_size, 2)

        # Check if this trade would risk too much given current drawdown state
        # Static drawdown: measured from initial balance (standard in most prop firms)
        floor_daily = self.start_of_day_equity * (1 - self.config.daily_dd_limit)
        remaining_daily = current_equity - floor_daily

        floor_total = self.initial_balance * (1 - self.config.max_dd_limit)
        remaining_total = current_equity - floor_total

        max_risk_allowed = min(remaining_daily, remaining_total)
        if max_risk_allowed <= 0:
            logger.debug("Signal rejected: no risk budget remaining")
            return None

        # Cap lot size if risk exceeds remaining budget
        actual_risk = lot_size * sl_pips * pip_value
        if actual_risk > max_risk_allowed:
            lot_size = max_risk_allowed / (sl_pips * pip_value)
            lot_size = round(lot_size, 2)
            if lot_size < self.instrument.min_lot:
                logger.debug("Signal rejected: lot size too small after risk cap")
                return None

        order = Order(
            id=self._next_order_id,
            symbol=signal.symbol,
            direction=direction,
            order_type=OrderType.MARKET,
            price=current_price,
            volume=lot_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            timestamp=signal.timestamp,
        )
        self._next_order_id += 1
        return order

    def on_trade_opened(self):
        self.open_position_count += 1

    def on_trade_closed(self, pnl: float, timestamp: datetime):
        self.open_position_count = max(0, self.open_position_count - 1)
        self.daily_pnl += pnl
        self.current_equity += pnl
        self._trading_days.add(timestamp.date())

        if self.current_equity > self.high_water_mark:
            self.high_water_mark = self.current_equity

        self._check_limits()

    def update_on_bar(self, timestamp: datetime, unrealized_pnl: float):
        current_date = timestamp.date()

        # Daily reset
        if self._last_reset_date is None or current_date > self._last_reset_date:
            if self._last_reset_date is not None:
                self.start_of_day_equity = self.current_equity
                self.daily_pnl = 0.0
            self._last_reset_date = current_date
            # Reset daily halt (but NOT permanent halt from total DD breach)
            if self._daily_halted and not self._permanently_halted:
                self._halted = False
                self._daily_halted = False
                self._halt_reason = ""

        # Check limits including unrealized PnL
        effective_equity = self.current_equity + unrealized_pnl
        self._check_limits_with_equity(effective_equity)

    def _check_limits(self):
        self._check_limits_with_equity(self.current_equity)

    def _check_limits_with_equity(self, equity: float):
        if self._permanently_halted:
            return

        # Overall drawdown check (permanent halt — account blown)
        overall_dd = (self.initial_balance - equity) / self.initial_balance
        if overall_dd >= self.config.max_dd_limit:
            self._halted = True
            self._permanently_halted = True
            self._halt_reason = f"Overall DD limit breached: {overall_dd:.2%} >= {self.config.max_dd_limit:.2%}"
            logger.warning(self._halt_reason)
            return

        # Daily drawdown check (temporary halt — resets next day)
        # FTMO daily DD = (start_of_day_equity - equity) / initial_balance
        if not self._daily_halted:
            daily_dd = (self.start_of_day_equity - equity) / self.initial_balance
            if daily_dd >= self.config.daily_dd_limit:
                self._halted = True
                self._daily_halted = True
                self._halt_reason = f"Daily DD limit breached: {daily_dd:.2%} >= {self.config.daily_dd_limit:.2%}"
                logger.warning(self._halt_reason)
                return

    def daily_drawdown_pct(self) -> float:
        if self.initial_balance <= 0:
            return 0.0
        return (self.start_of_day_equity - self.current_equity) / self.initial_balance

    def overall_drawdown_pct(self) -> float:
        if self.initial_balance <= 0:
            return 0.0
        return (self.initial_balance - self.current_equity) / self.initial_balance
