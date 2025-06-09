from enum import Enum


class OrderType(str, Enum):
    """
    The different types of orders in the Monger system.

    :cvar ENTRY: An entry order.
    :cvar TAKE_PROFIT: A take profit order.
    :cvar STOP_LOSS: A stop loss order.
    :cvar EXIT: An exit order.
    :cvar EMERGENCY_EXIT: An emergency exit order.
    """

    ENTRY = "entry"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    EXIT = "exit"
    EMERGENCY_EXIT = "emergency_exit"
    DANGLING_SHARES = "dangling_shares"


class PositionSide(str, Enum):
    """
    The direction of a position.

    :cvar LONG: A long position.
    :cvar SHORT: A short position.
    """

    LONG = "long"
    SHORT = "short"


class OrderStatus(str, Enum):
    """
    The status of an order.

    :cvar SUBMITTED: The order is pending.
    :cvar FILLED: The order has been filled.
    :cvar CANCELED: The order has been canceled.
    """

    PRE_SUBMITTED = "PreSubmitted"
    SUBMITTED = "Submitted"
    FILLED = "Filled"
    CANCELED = "Cancelled"


class OrderAction(str, Enum):
    """
    The action of an order.

    :cvar BUY: A buy order.
    :cvar SELL: A sell order.
    """

    BUY = "BUY"
    SELL = "SELL"
