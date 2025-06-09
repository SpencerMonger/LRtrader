"""
The error module defines the custom error classes for the application.
"""


class MongerError(Exception):
    """
    Base class for all Monger errors.
    """


class InvalidExecutionError(MongerError):
    """
    Raised when an invalid execution is attempted.

    This error is raised when a `handle_***` method is called (originating
    from the `orderStatus` event from the IB API), but an irreconcilable
    error occurs, related to the state of the execution event being handled.
    """


class OrderDoesNotExistError(MongerError):
    """
    Raised when an order does not exist in the order pool.
    """


class CannotModifyFilledOrderError(MongerError):
    """
    Raised when a filled or partially filled order is attempted to be modified.
    """


class StopLossCooldownIsActiveError(MongerError):
    """
    Raised when a stop loss cooldown is active and a new order is attempted to be placed.
    """


class TradeLockedError(MongerError):
    """
    Raised when a trade is locked and cannot be modified.
    """
