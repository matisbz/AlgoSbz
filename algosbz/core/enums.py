from enum import Enum, auto


class Direction(Enum):
    LONG = auto()
    SHORT = auto()


class OrderType(Enum):
    MARKET = auto()
    STOP = auto()


class SignalAction(Enum):
    ENTER_LONG = auto()
    ENTER_SHORT = auto()
    EXIT = auto()
    NO_ACTION = auto()


class ExitReason(Enum):
    STOP_LOSS = "sl"
    TAKE_PROFIT = "tp"
    SIGNAL = "signal"
    RISK_MANAGER = "risk_manager"
    END_OF_DATA = "end_of_data"
