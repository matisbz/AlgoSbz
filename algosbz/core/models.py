from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .enums import Direction, OrderType, SignalAction, ExitReason


@dataclass(frozen=True)
class Signal:
    action: SignalAction
    symbol: str
    timestamp: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Order:
    id: int
    symbol: str
    direction: Direction
    order_type: OrderType
    price: float
    volume: float  # lot size
    stop_loss: float
    take_profit: Optional[float]
    timestamp: datetime


@dataclass
class Fill:
    order_id: int
    fill_price: float
    spread_cost: float
    slippage: float
    commission: float
    timestamp: datetime


@dataclass
class Position:
    id: int
    symbol: str
    direction: Direction
    entry_price: float
    volume: float
    stop_loss: float
    take_profit: Optional[float]
    entry_time: datetime
    unrealized_pnl: float = 0.0


@dataclass
class Trade:
    id: int
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    volume: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pips: float
    risk_reward: float
    stop_loss: float
    take_profit: Optional[float]
    exit_reason: ExitReason
    commission: float = 0.0
