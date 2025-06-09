from datetime import datetime
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field, PrivateAttr, computed_field

from error import InvalidExecutionError, TradeLockedError

from .enums import OrderType, PositionSide
from .market import MarketData
from .order import MongerOrder


class Trade(BaseModel):
    """
    A trade is a collection of entry executions chained together with a single exit execution.

    When the model is triggering buy signals, we continue to pool subsequent executions together
    into a single trade, so long as the executions are with in a pre-defined threshold, referred
    to as the `trade_threshold`. For example, if the trade threshold is 60 seconds, then we will
    continue to pool subsequent buy signals together into a single trade so long as the time
    between the executions is less than 60 seconds.

    :param int trade_id: The trade ID. This is created from the order ID of the first execution
        in the trade.
    :param side PositionSide: The side of the trade.
    :param float size: The size of the trade in shares.
    :param float avg_price: The average price of the trade.
    :param float created_at: The time the trade was created (fill time of fist execution).
    :param float updated_at: The time the trade was last updated (fill time of last execution).
    """

    trade_id: int = Field(...)
    side: PositionSide = Field(...)
    size: float = Field(...)
    avg_price: float = Field(...)
    take_profit_anchor: float = Field(...)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    take_profit_order: Optional[MongerOrder] = Field(None)
    stop_loss_order: Optional[MongerOrder] = Field(None)
    exit_order: Optional[MongerOrder] = Field(None)

    take_profit_filled: bool = Field(False)

    _trade_threshold: int = PrivateAttr(...)
    _hold_threshold: int = PrivateAttr(...)

    _first_execution: datetime = PrivateAttr(default_factory=datetime.now)
    _last_execution: datetime = PrivateAttr(default_factory=datetime.now)

    # Track number of shares filled by each entry and exit to ensure that
    # we can handle duplicate messages gracefully
    _entry_fills_sizes: dict[int, float] = PrivateAttr(default_factory=dict)
    _entry_avg_prices: dict[int, float] = PrivateAttr(default_factory=dict)
    _exit_fills_sizes: dict[int, float] = PrivateAttr(default_factory=dict)
    _exit_avg_prices: dict[int, float] = PrivateAttr(default_factory=dict)

    _take_profit_target: float = PrivateAttr(...)
    _stop_loss_target: float = PrivateAttr(...)

    @computed_field
    def is_locked(self) -> bool:
        """
        Check if we are able to add more entries to the trade.

        :return bool: True if the trade is available to add an entry, False otherwise.
        """
        now = datetime.now()
        return (now - self.updated_at).total_seconds() > self._trade_threshold

    @computed_field
    def is_expired(self) -> bool:
        """
        Check if the trade is expired (i.e. needs to be sold).

        :return bool: True if the trade is expired, False otherwise.
        """
        now = datetime.now()

        # Get the midpoint of the first and last executions to use as the anchor point
        # for the expiration time
        midpoint = self._first_execution + (self._last_execution - self._first_execution) / 2
        return (now - midpoint).total_seconds() > self._hold_threshold

    @computed_field
    def realized_pnl(self) -> float:
        """
        Calculate the realized P/L for this trade based on filled entries and exits.
        For LONG positions: (exit_price - entry_price) * size
        For SHORT positions: (entry_price - exit_price) * size

        :return float: The realized P/L for this trade, rounded to 2 decimal places.
        """

        if not self._exit_fills_sizes:
            return 0.0

        # Calculate total exit value (price * size for each exit)
        total_exit_value = sum(
            size * price
            for size, price in zip(self._exit_fills_sizes.values(), self._exit_avg_prices.values())
        )
        total_exit_size = sum(self._exit_fills_sizes.values())

        # Calculate total entry value for the corresponding exit size
        total_entry_value = sum(
            size * price
            for size, price in zip(
                self._entry_fills_sizes.values(), self._entry_avg_prices.values()
            )
        )
        total_entry_size = sum(self._entry_fills_sizes.values())

        # Calculate the average prices
        avg_exit_price = total_exit_value / total_exit_size if total_exit_size > 0 else 0
        avg_entry_price = total_entry_value / total_entry_size if total_entry_size > 0 else 0

        # Calculate P/L based on position side
        if self.side == PositionSide.LONG:
            pnl = (avg_exit_price - avg_entry_price) * total_exit_size
        else:  # SHORT
            pnl = (avg_entry_price - avg_exit_price) * total_exit_size

        return round(pnl, 2)

    @property
    def open_orders(self) -> list[MongerOrder]:
        """
        Get a list of open orders for this trade.

        :return list[MongerOrder]: The open orders.
        """
        open_orders = list()

        if self.exit_order:
            open_orders.append(self.exit_order)
        if self.take_profit_order:
            open_orders.append(self.take_profit_order)
        if self.stop_loss_order:
            open_orders.append(self.stop_loss_order)

        return open_orders

    def get_take_profit_price(self) -> Optional[float]:
        """
        Get the value of the TAKE PROFIT order SHOULD BE for this trade.
        Calculates based on percentage if _take_profit_target <= 1.0, otherwise uses flat amount.

        :return Optional[float]: The take profit price.
        """
        if self.take_profit_filled:
            return None

        # Check if the target is a percentage (<= 1.0) or a flat amount (> 1.0)
        if self._take_profit_target <= 1.0:
            # Percentage calculation
            if self.side == PositionSide.LONG:
                return self.take_profit_anchor * (1 + self._take_profit_target)
            else: # SHORT
                return self.take_profit_anchor * (1 - self._take_profit_target)
        else:
            # Flat amount calculation (original logic)
            if self.side == PositionSide.LONG:
                return self.take_profit_anchor + self._take_profit_target
            else: # SHORT
                return self.take_profit_anchor - self._take_profit_target

    def get_take_profit_size(self) -> Optional[float]:
        """
        Get the size of the TAKE PROFIT order SHOULD BE for this trade.

        :return Optional[float]: The take profit size.
        """
        if self.take_profit_filled:
            return None

        return self.size // 2

    def get_stop_loss_price(self, market_data: MarketData) -> Optional[float]:
        """
        Get the value of the STOP LOSS order SHOULD BE for this trade.
        Calculates based on percentage if _stop_loss_target <= 1.0, otherwise uses flat amount.

        :return Optional[float]: The stop loss price.
        """
        # Check if the target is a percentage (<= 1.0) or a flat amount (> 1.0)
        if self._stop_loss_target <= 1.0:
            # Percentage calculation
            if self.side == PositionSide.LONG:
                return self.take_profit_anchor * (1 - self._stop_loss_target)
            else: # SHORT
                return self.take_profit_anchor * (1 + self._stop_loss_target)
        else:
            # Flat amount calculation (original logic)
            if self.side == PositionSide.LONG:
                return self.take_profit_anchor - self._stop_loss_target
            else: # SHORT
                return self.take_profit_anchor + self._stop_loss_target

    def get_stop_loss_size(self) -> Optional[float]:
        """
        Get the size of the STOP LOSS order SHOULD BE for this trade.

        :return Optional[float]: The stop loss size.
        """
        return self.size

    @classmethod
    def from_entry(
        cls,
        order_id: int,
        side: PositionSide,
        size: float,
        avg_price: float,
        trade_threshold: int,
        hold_threshold: int,
        take_profit_target: float,
        stop_loss_target: float,
        **kwargs,
    ) -> "Trade":
        """
        Create a new trade from an entry execution.

        :param int order_id: The order ID of the execution.
        :param PositionSide side: The side of the execution.
        :param float size: The size of the execution.
        :param float avg_price: The average price of the execution.
        :param int trade_threshold: The time threshold for pooling executions into a single trade.
        :param int hold_threshold: The time threshold for holding a trade before selling.
        """
        trade = cls(
            trade_id=order_id,
            side=side,
            size=size,
            avg_price=avg_price,
            take_profit_anchor=avg_price,
            **kwargs,
        )
        trade._trade_threshold = trade_threshold
        trade._hold_threshold = hold_threshold

        trade._entry_fills_sizes[order_id] = size
        trade._entry_avg_prices[order_id] = avg_price

        trade._take_profit_target = take_profit_target
        trade._stop_loss_target = stop_loss_target

        return trade

    def add_exit_order(self, order: MongerOrder) -> None:
        """
        Add an exit execution to the trade.

        :param MongerOrder order: The exit order.

        :raises InvalidExecutionError: If the trade is already expired.
        """
        if not self.is_expired:
            raise InvalidExecutionError("Cannot exit a trade that is not expired.")

        self.exit_order = order

    def close_exit_order(self) -> None:
        """
        Close the exit order.
        """
        self.exit_order = None

    def set_take_profit_order(self, order: MongerOrder) -> None:
        """
        Set the take profit order for this trade.
        """
        if order.order_type != OrderType.TAKE_PROFIT:
            raise InvalidExecutionError("Can only set take profit orders as take profit.")
        self.take_profit_order = order

    def set_stop_loss_order(self, order: MongerOrder) -> None:
        """
        Set the stop loss order for this trade.
        """
        if order.order_type != OrderType.STOP_LOSS:
            raise InvalidExecutionError("Can only set stop loss orders as stop loss.")
        self.stop_loss_order = order

    def clear_take_profit_order(self) -> None:
        """
        Clear the take profit order for this trade.
        """
        self.take_profit_order = None

    def clear_stop_loss_order(self) -> None:
        """
        Clear the stop loss order for this trade.
        """
        self.stop_loss_order = None

    def add(self, order_id: int, side: PositionSide, size: float, avg_price: float) -> None:
        """
        Add an entry execution to the trade.

        :param int order_id: The order ID of the execution.
        :param PositionSide side: The side of the execution.
        :param float size: The size of the execution.
        :param float avg_price: The average price of the execution.

        :raises InvalidExecutionError: If the side of the execution does not match the trade side.
        :raises ValueError: If the trade is not available to add an entry.
        """

        if self.side != side:
            raise InvalidExecutionError("The side of the execution does not match the trade side.")

        if self.is_locked:
            raise TradeLockedError("The trade is not available to add an entry.")

        # Update the fills with the new execution
        self._entry_fills_sizes[order_id] = size
        self._entry_avg_prices[order_id] = avg_price

        logger.debug(f"TRADE ID {self.trade_id} -- ENTRY FILL SIZES: {self._entry_fills_sizes}")
        logger.debug(f"TRADE ID {self.trade_id} -- ENTRY FILL PRICES: {self._entry_avg_prices}")

        # Compute the new size using the entry_fills
        shares_added = sum(self._entry_fills_sizes.values())
        shares_removed = sum(self._exit_fills_sizes.values())
        self.size = shares_added - shares_removed

        # Compute the new average price using a weighted average
        new_avg_price = round(
            sum(
                size * price
                for size, price in zip(
                    self._entry_fills_sizes.values(), self._entry_avg_prices.values()
                )
            )
            / shares_added,
            2,
        )

        # Update the average price to reflect the new execution
        self.avg_price = new_avg_price
        self.updated_at = datetime.now()
        self._last_execution = datetime.now()

    def remove(self, order_id: int, num_shares: float, exit_price: float) -> float:
        """
        Remove shares from a trade (as a result of an exit order being filled).

        :param int order_id: The order ID of the exit.
        :param float num_shares: The number of shares to remove.
        :param float exit_price: The price at which the shares were exited.
        :return float: The amount of `num_shares` that is left over.
        """
        # Get the number of exit fills already alloted to this order
        exit_fills = self._exit_fills_sizes.get(order_id, 0)

        # Using the num shares, figure out how many new filled shares
        # are being introduced by this removal
        new_fills = min(self.size, num_shares - exit_fills)

        # If all of these shares have been accounted for in this trade
        # already, then we can just return the number of shares left over
        if new_fills == 0:
            return new_fills

        # Update the number of exit fills and prices for this order
        self._exit_fills_sizes[order_id] = exit_fills + new_fills
        self._exit_avg_prices[order_id] = exit_price

        # Update the trade size
        self.size = max(0, self.size - new_fills)

        return num_shares - (new_fills + exit_fills)
