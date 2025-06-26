from datetime import datetime, timedelta, time
from typing import Any, Optional

from ibapi.order import Order as IBOrder
from pydantic import BaseModel, Field, computed_field
import pytz

from src.error import CannotModifyFilledOrderError, InvalidExecutionError, OrderDoesNotExistError

from .enums import OrderAction, OrderStatus, OrderType


class MongerOrder(BaseModel):
    """
    The MongerOrder model represents an order submitted by a Monger.

    :param int order_id: The order ID of the order.
    :param OrderType order_type: The type of the order.
    :param OrderAction action: The action of the order (BUY or SELL).
    :param float size: The size of the order.
    :param float avg_price: The average price of the order.
    :param float created_at: The time the order was created.

    NOTE: This class is replacing the unit position.
    """

    order_id: int = Field(...)
    order_type: OrderType = Field(...)
    action: OrderAction = Field(...)
    size: float = Field(...)
    limit_price: float = Field(...)

    filled: float = Field(0.0)
    avg_price: float = Field(0.0)
    status: OrderStatus = Field(OrderStatus.SUBMITTED)
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def ib_order_type(self) -> str:
        """
        Get the IB order type string.

        :return str: The IB order type string.
        """
        match self.order_type:
            case OrderType.ENTRY:
                return "LMT"
            case OrderType.TAKE_PROFIT:
                return "LMT"
            case OrderType.STOP_LOSS:
                return "STP LMT"
            case OrderType.EXIT:
                return "LMT"
            case OrderType.EMERGENCY_EXIT:
                return "LMT"
            case OrderType.DANGLING_SHARES:
                return "LMT"

    def requires_update(self, new_size: float, new_price: float) -> bool:
        """
        Check if the order requires an update.

        :param float new_size: The new size of the order.
        :param float new_price: The new price of the order.
        :return bool: True if the order requires an update, False otherwise.
        """
        if round(new_size, 2) != round(self.size, 2):
            return True

        if round(new_price, 2) != round(self.limit_price, 2):
            return True

        return False

    @computed_field
    def ib_order(self) -> Any:
        """
        Convert the MongerOrder to an IBOrder.

        :return IBOrder: The IBOrder instance.
        """
        ib_order = IBOrder()
        ib_order.action = self.action.value
        ib_order.orderType = self.ib_order_type
        ib_order.totalQuantity = int(self.size)
        
        # Only set limit price for non-market orders
        if self.ib_order_type != "MKT":
            ib_order.lmtPrice = round(self.limit_price, 2)

        # Explicitly set etradeOnly to False to avoid default issues
        ib_order.etradeOnly = False

        # Hardcode outsideRth to True since this codebase ONLY trades outside regular market hours
        ib_order.outsideRth = True

        ib_order.transmit = True

        # Default TIF to DAY
        ib_order.tif = "DAY"

        # Set GTD with 60 second expiration for ENTRY orders
        if self.order_type == OrderType.ENTRY:
            expiration = datetime.now(pytz.UTC) + timedelta(seconds=60) # 60 seconds
            expiration_str = expiration.strftime("%Y%m%d-%H:%M:%S")
            ib_order.tif = "GTD"
            ib_order.goodTillDate = expiration_str

        # Use GTD with 10 second expiration ONLY for EXIT and DANGLING_SHARES
        elif (
            self.order_type == OrderType.EXIT
            or self.order_type == OrderType.DANGLING_SHARES
        ):
            expiration = datetime.now(pytz.UTC) + timedelta(seconds=10) # 10 seconds
            expiration_str = expiration.strftime("%Y%m%d-%H:%M:%S")
            ib_order.tif = "GTD"
            ib_order.goodTillDate = expiration_str

        # Add GTC for EMERGENCY_EXIT orders to allow outside RTH execution
        elif self.order_type == OrderType.EMERGENCY_EXIT:
            ib_order.tif = "GTC"

        if self.order_type == OrderType.STOP_LOSS:
            # Set the stop price
            direction = 1 if self.action == OrderAction.BUY else -1
            gap = direction * 0.10
            ib_order.auxPrice = round(self.limit_price, 2)
            ib_order.lmtPrice = round(self.limit_price + gap, 2)

        return ib_order


class PendingOrderPool(BaseModel):
    """
    The pending order pool is used to manage the set of orders currently submitted to the broker.

    NOTE: Our goal with the implementation of this class is to fully replace the unit position and
    the current dict based organization of unit positions and exit orders in the position class.
    Each instantiation of the Position model should have it's own PendingOrderPool private
    attribute. The position model will provide an interface for using the underling order pool to
    manage it's trades and pending orders effectively, based on the events handled by the
    OrderExecutor.

    :param index dict[int, MongerOrder]: A mapping of order IDs to the MongerOrder.
    :param int count: The number of pending orders in the pool.
    """

    index: dict[int, MongerOrder] = Field(default_factory=dict)

    def __getitem__(self, order_id: int) -> MongerOrder:
        """
        Get an order by its ID.

        :param int order_id: The order ID.
        :return MongerOrder: The order.
        """
        try:
            return self.index[order_id]
        except KeyError:
            raise OrderDoesNotExistError(f"Order ID {order_id} does not exist in the pool.")

    @computed_field
    def count(self) -> int:
        """
        The number of pending orders in the pool.
        """
        return len(self.index)

    @computed_field
    def orders(self) -> list[MongerOrder]:
        """
        The list of pending orders in the pool.
        """
        return list(self.index.values())

    @computed_field
    def stop_loss_orders(self) -> Optional[MongerOrder]:
        """
        Retrieve the stop loss order in the pool.

        :return Optional[MongerOrder]: The stop loss order.
        """

        # Sort these orders by created_at date (descending, newest first),
        # we want to return the most recent stop loss
        sorted_orders = sorted(self.orders, key=lambda order: order.created_at, reverse=True)
        return [order for order in sorted_orders if order.order_type == OrderType.STOP_LOSS]

    @computed_field
    def take_profit_orders(self) -> Optional[MongerOrder]:
        """
        Retrieve the take profit order in the pool.

        :return Optional[MongerOrder]: The take profit order.
        """
        sorted_orders = sorted(self.orders, key=lambda order: order.created_at, reverse=True)
        return [order for order in sorted_orders if order.order_type == OrderType.TAKE_PROFIT]

    @computed_field
    def emergency_exit_order(self) -> Optional[MongerOrder]:
        """
        Retrieve the emergency exit order in the pool.

        :return Optional[MongerOrder]: The emergency exit order.
        """
        return next(
            (order for order in self.orders if order.order_type == OrderType.EMERGENCY_EXIT), None
        )

    def remove(self, order_id: int) -> None:
        """
        Remove an order from the pool.
        """
        self.index.pop(order_id, None)

    def submit_order(self, order: MongerOrder) -> MongerOrder:
        """
        Submit an order to the pool.

        NOTE: This is our gaurd that we must call BEFORE submitting any order to IBKR. We
        will check here that the order is valid and can be submitted.

        :param int order_id: The ID of the order that was submitted.
        :param OrderType order_type: The type of the order.
        :param float size: The size of the order.
        :param float avg_price: The average price of the order.
        :return MongerOrder: The order that was submitted.

        :raises InvalidExecutionError: If the order type does not match the existing order.
        :raises InvalidExecutionError: If the order is already filled or canceled.
        """
        existing_order = self.index.get(order.order_id, None)

        # If order doesn't exist, create a new order
        if existing_order:
            # Ensure that the order_type is the same
            if order.order_type != existing_order.order_type:
                raise InvalidExecutionError(
                    f"Order ID: {order.order_id} exists in pool as {order.order_type}, but received"
                    " submitted event as {order_type}."
                )

            # Ensure that the order action is the same
            if order.action != existing_order.action:
                raise InvalidExecutionError(
                    f"Order ID: {order.order_id} exists in pool as {order.action}, but received "
                    "submitted event as {order_action}."
                )

        # Ensure that the size is not going to be less than the filled amount
        if order.size <= 0:
            raise InvalidExecutionError(
                f"Order ID: {order.order_id} cannot be submitted with 0 shares."
            )

        if order.filled > 0:
            raise CannotModifyFilledOrderError(
                f"Order ID: {order.order_id} cannot be submitted with a "
                f"filled amount of {order.filled}."
            )

        # Set the order in the index
        self.index[order.order_id] = order
        return order

    def handle_submitted(self, order_id: int, filled: float, avg_price: float) -> MongerOrder:
        """
        Handle a submitted order event by ensuring it is updated in the pool.

        :param int order_id: The ID of the order that was submitted.
        :param float filled: The number of shares/contracts filled.
        :param float avg_price: The average price at which the order was filled.
        :return Optional[MongerOrder]: The order that was submitted.

        :raises InvalidExecutionError: If the order does not exist in the pool.
        """

        order = self.index.get(order_id, None)
        if not order:
            raise OrderDoesNotExistError(
                f"Order ID {order_id} does not exist in the pending pool. "
                "Cannot handle submitted event for a non-existent order."
            )

        if filled < 0:
            raise InvalidExecutionError(
                f"Order ID {order_id} cannot be submitted with a negative filled amount."
            )

        # If we have a partial fill, let's be sure to update that in the order
        if filled > 0:
            order.filled = filled
            order.avg_price = avg_price

        self.index[order_id] = order
        return order

    def handle_filled(self, order_id: int, filled: float, avg_price: float) -> MongerOrder:
        """
        Handle a filled order by updating its filled quantity and average price,
        and removing it from the pending pool.

        :param int order_id: The ID of the order that was filled.
        :param float filled: The number of shares/contracts filled.
        :param float avg_price: The average price at which the order was filled.
        :return MongerOrder: The order that was filled

        :raises InvalidExecutionError: If the order does not exist in the pool.
        :raises InvalidExecutionError: If the average price is invalid.
        """
        order = self.index.pop(order_id, None)
        if not order:
            raise OrderDoesNotExistError(
                f"Order ID {order_id} does not exist in the pending pool. "
                "Cannot handle filled event for a non-existent order."
            )

        # The filled and average price values come from IB in the current state
        # of the order, so we just need to set the values, not calculate them.
        order.filled = filled
        order.avg_price = avg_price
        order.status = OrderStatus.FILLED

        return order

    def handle_cancelled(self, order_id: int, filled: float, avg_price: float) -> MongerOrder:
        """
        Handle a cancelled order by removing it from the pending pool.

        :param int order_id: The ID of the order that was cancelled.
        :return MongerOrder: The order that was cancelled.

        :raises InvalidExecutionError: If the order does not exist in the pool.
        :raises InvalidExecutionError: If the order is already filled.
        """
        order = self.index.pop(order_id, None)
        if not order:
            raise OrderDoesNotExistError(f"Cannot cancel Order ID {order_id} as it does not exist.")

        order.filled = filled
        order.avg_price = avg_price
        order.status = OrderStatus.CANCELED

        return order
