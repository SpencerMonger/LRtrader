"""
The Position model represents a single position held by a client.
"""

from datetime import datetime
from typing import Optional, Tuple, Any

from loguru import logger
from pydantic import BaseModel, Field, PrivateAttr, computed_field

from error import CannotModifyFilledOrderError, InvalidExecutionError, StopLossCooldownIsActiveError

from .assignment import TraderAssignment
from .enums import OrderAction, OrderType, PositionSide
from .order import MongerOrder, PendingOrderPool
from .trade import Trade


class Position(BaseModel):
    """
    Data structure for managing the position of a client.

    The position is thread-safe and is scoped to the context of a single
    ticker.

    :param PendingOrderPool pool: The pool of pending orders.
    :param dict[int, Trade] trades: A mapping of trade IDs to the Trade.

    :param side PositionSide: The side of the position.
    :param float size: The size of the position.
    :param float avg_price: The average price of the position.
    :param Optional[MongerOrder] stop_loss_order: The stop loss order.
    :param Optional[MongerOrder] take_profit_order: The take profit order.
    :param Optional[MongerOrder] emergency_exit_order: The emergency exit order.
    """

    assignment: TraderAssignment = Field(...)

    pool: PendingOrderPool = Field(default_factory=PendingOrderPool)
    trades: dict[int, Trade] = Field(default_factory=dict)

    orders_to_cancel: list[MongerOrder] = Field(default_factory=list)

    true_share_count: int = Field(default=0)

    _contract_id: Optional[int] = PrivateAttr(None)
    _cooldown_triggered: Optional[datetime] = PrivateAttr(None)
    _position_side_cached: Optional[PositionSide] = PrivateAttr(None)
    _portfolio_manager: Optional[Any] = PrivateAttr(None)

    _realized_pnls: list[float] = PrivateAttr(default_factory=list)

    # Add portfolio_manager to model_post_init for proper Pydantic initialization
    def model_post_init(self, __context: Any) -> None:
        # If context provides a portfolio_manager, store it.
        if __context and 'portfolio_manager' in __context:
            self._portfolio_manager = __context['portfolio_manager']
        
        # Existing logic if needed, or just pass if model_post_init wasn't previously defined
        # super().model_post_init(__context) # Uncomment if inheriting from a class with model_post_init

    @computed_field
    def contract_id(self) -> int:
        if not self._contract_id:
            return 0
        return self._contract_id

    @computed_field
    def side(self) -> PositionSide:
        """
        The side of the position.
        """
        if self.size == 0 and self._position_side_cached is None:
            return None

        # Otherwise, get a trade and return the side
        try:
            trade = next(iter(self.trades.values()))
            self._position_side_cached = trade.side
            return self._position_side_cached
        except StopIteration:
            return self._position_side_cached

    @computed_field
    def size(self) -> float:
        """
        The size of the position.
        """
        # We know all trades are the same side so we can just sum them
        return sum(trade.size for trade in self.trades.values())

    @computed_field
    def relevant_position_size(self) -> float:
        """
        The size of the position, adjusted for the side.
        """

        settled_shares = self.size if self.side == PositionSide.LONG else -self.size
        return settled_shares

    @computed_field
    def avg_price(self) -> float:
        """
        The average price of the position.
        """

        # Get all the trades and computed the weighted average price based
        # on their sizes and average prices
        size = 0
        total_value = 0
        for trade in self.trades.values():
            size += trade.size
            total_value += trade.size * trade.avg_price

        return total_value / size if size > 0 else 0.0

    @computed_field
    def stop_loss_orders(self) -> list[MongerOrder]:
        """
        Retrieve the stop loss order in the position.

        :return Optional[MongerOrder]: The stop loss order.
        """
        return self.pool.stop_loss_orders

    @computed_field
    def take_profit_orders(self) -> list[MongerOrder]:
        """
        Retrieve the take profit order in the position.

        :return Optional[MongerOrder]: The take profit order.
        """
        return self.pool.take_profit_orders

    @computed_field
    def emergency_exit_order(self) -> Optional[MongerOrder]:
        """
        Retrieve the emergency exit order in the position.

        :return Optional[MongerOrder]: The emergency exit order.
        """
        return self.pool.emergency_exit_order

    @computed_field
    def is_empty(self) -> bool:
        """
        Check if the position is empty.

        :return bool: True if the position is empty, False otherwise.
        """

        return not bool(self.trades)

    @computed_field
    def open_orders(self) -> list[int]:
        """
        Retrieve the open orders in the position.

        :return list[int]: The list of open orders.
        """

        orders = self.pool.orders
        return [order.order_id for order in orders]

    @property
    def dangling_shares_order(self) -> Optional[MongerOrder]:
        """
        Retrieve the dangling shares order in the position.
        """

        for order_id in self.open_orders:
            if self.get_order_type_by_id(order_id) == OrderType.DANGLING_SHARES:
                return self.pool[order_id]

    @property
    def entry_action(self) -> Optional[OrderAction]:
        """
        Retrieve the action that should be taken for the next entry.

        :return OrderAction: The action taken for adding to the position.
        """
        if self.side is None:
            return None
        return OrderAction.BUY if self.side == PositionSide.LONG else OrderAction.SELL

    @property
    def exit_action(self) -> Optional[OrderAction]:
        """
        Retrieve the action that should be taken for the next exit.

        :return OrderAction: The action taken for exiting the position.
        """
        if self.side is None:
            return None
        return OrderAction.SELL if self.side == PositionSide.LONG else OrderAction.BUY

    @property
    def stop_loss_size(self) -> float:
        """
        Retrieve the size that should be set for the stop loss based on the current position.

        :return float: The size of the stop loss order.
        """
        return self.size

    @property
    def take_profit_size(self) -> float:
        """
        Retrieve the size that should be set for the take profit based on the current position.

        :return float: The size of the take profit order.
        """
        # Get all the trades in the position that DON'T have a take profit ID
        trades = [trade for trade in self.all_trades if not trade.take_profit_order_id]
        return sum(trade.size for trade in trades) // 2

    @property
    def open_trade(self) -> Optional[Trade]:
        """
        Retrieve the open trade in the position.

        :return Optional[Trade]: The open trade.
        """
        return next((trade for trade in self.trades.values() if not trade.is_locked), None)

    @property
    def trade_to_close(self) -> Optional[Trade]:
        """
        Retrieve the trade that needs to be closed.

        :return Optional[Trade]: The trade that needs to be closed.
        """
        return next((trade for trade in self.all_trades if trade.is_expired), None)

    @property
    def all_trades(self) -> list[Trade]:
        """
        Retrieve all trades in the position, sorted by creation time in ascending order.

        :return list[Trade]: The list of trades.
        """
        return sorted(self.trades.values(), key=lambda trade: trade.created_at)

    @property
    def in_cooldown(self) -> bool:
        """
        Check if the stop loss is on cooldown.

        :return bool: True if the stop loss is on cooldown, False otherwise.
        """
        if not self._cooldown_triggered:
            return False

        time_since_trigger = (datetime.now() - self._cooldown_triggered).total_seconds()
        is_cooldown = time_since_trigger < self.assignment.trade_threshold

        if not is_cooldown:
            self._cooldown_triggered = None

        return is_cooldown

    @property
    def realized_pnls(self) -> list[float]:
        return self._realized_pnls

    def set_cancel_status(self, order: MongerOrder) -> None:
        """
        Set the status of the order to cancelled.

        :param MongerOrder order: The order to cancel.
        """
        # Check if the order is currently in the orders to cancel
        cancel_ids = [order.order_id for order in self.orders_to_cancel]
        if order.order_id in cancel_ids:
            return

        self.orders_to_cancel.append(order)

    def cooldown_trigger(self) -> None:
        """
        Set the stop loss as filled.
        """
        self._cooldown_triggered = datetime.now()

    def get_order_type_by_id(self, order_id: int) -> OrderType:
        """
        Check the type of the order.

        :return OrderType: The type of the order.
        """
        try:
            order = self.pool[order_id]
        except KeyError:
            raise ValueError("Order ID not found in pool")

        return order.order_type

    def get_trade_by_stop_loss_order_id(self, order_id: int) -> Optional[Trade]:
        """
        Retrieve the trade associated with the stop loss order.

        :param int order_id: The order ID of the stop loss order.
        :return Optional[Trade]: The trade associated with the stop loss order.
        """
        for trade in self.all_trades:
            if trade.stop_loss_order and trade.stop_loss_order.order_id == order_id:
                return trade

    def get_trade_by_take_profit_order_id(self, order_id: int) -> Optional[Trade]:
        """
        Retrieve the trade associated with the take profit order.

        :param int order_id: The order ID of the take profit order.
        :return Optional[Trade]: The trade associated with the take profit order.
        """
        for trade in self.all_trades:
            if trade.take_profit_order and trade.take_profit_order.order_id == order_id:
                return trade

    def add_to_position(
        self,
        order_id: int,
        action: OrderAction,
        size: float,
        avg_price: float,
    ) -> "Trade":
        """
        Add an entry to the position. If no trade is open, a new trade is created.

        :param int order_id: The order ID of the entry.
        :param OrderAction action: The action of the entry.
        :param float size: The size of the entry.
        :param float avg_price: The average price of the entry.
        :return Trade: The trade the entry was added to.
        """
        side = PositionSide.LONG if action == OrderAction.BUY else PositionSide.SHORT

        # If we have a position, assert that they are the same side
        if self.size > 0:
            if self.side != side:
                raise InvalidExecutionError(
                    "The side of the entry does not match the side of the position."
                )

        if trade := self.open_trade:
            logger.debug(f"adding order ID: {order_id} to trade ID: {trade.trade_id}")
            trade.add(order_id, side, size, avg_price)
        else:
            logger.debug(f"creating new trade ID: {order_id}")
            trade = Trade.from_entry(
                order_id=order_id,
                side=side,
                size=size,
                avg_price=avg_price,
                trade_threshold=self.assignment.trade_threshold,
                hold_threshold=self.assignment.hold_threshold,
                take_profit_target=self.assignment.take_profit_target,
                stop_loss_target=self.assignment.stop_loss_target,
            )

        self.trades[trade.trade_id] = trade
        return trade

    def _close_trade(self, trade: Trade) -> None:
        logger.debug(f"CLOSING TRADE WITH REALIZED P/L: {trade.realized_pnl}")
        self._realized_pnls.append(trade.realized_pnl)
        self.trades.pop(trade.trade_id)

        # If all the trades are closed, then uncache the position side
        if len(self.trades) == 0:
            self._position_side_cached = None

    def remove_from_position(
        self,
        order_id: int,
        num_shares: float,
        price: float,
        is_stop_loss: bool = False,
        is_take_profit: bool = False,
        is_manual: bool = False,
    ) -> None:
        """
        Remove shares from the position by iterating over the trades.

        NOTE: This is useful for take profit orders and stop loss orders.

        :param int order_id: The order ID of the exit.
        :param float num_shares: The number of shares to remove.
        :param bool is_stop_loss: True if the order is a stop loss, False otherwise.
        :param bool is_take_profit: True if the order is a take profit, False otherwise.
        :return list[MongerOrder]: A list of orders that need to be cancelled.

        :raises InvalidExecutionError: If the order filled with more shares than were currently
            in the position.
        """
        for trade in self.all_trades:
            if is_take_profit:
                # Check if the trade has a different take profit order
                if trade.take_profit_order and trade.take_profit_order.order_id != order_id:
                    continue

            if is_stop_loss:
                # Check if the trade has a different stop loss order
                if trade.stop_loss_order and trade.stop_loss_order.order_id != order_id:
                    continue

            # Otherwise, if it has no take-profit, is from this take-profit, or is a stop-loss
            # order, then we want to remove shares from the trade
            num_shares = trade.remove(order_id, num_shares, price)

            # If we have filled a take profit and we hvae cleared all the shares for the order
            # then we want to make sure that the trade is marked as take profited
            if is_take_profit and num_shares == 0:
                trade.take_profit_filled = True

            # If the trade has been completely emptied, then we can remove it from the position
            if trade.size == 0:
                for order_to_cancel in trade.open_orders:
                    self.set_cancel_status(order_to_cancel)
                self._close_trade(trade)

            if num_shares == 0:
                break

        # If we get to the end and still have shares left over, that's a problem, we will
        # need to remedy this by placing an inverse order to close out the position

    def submit_order(self, order: MongerOrder, parent_trade: Optional[Trade] = None) -> None:
        """
        Submit an order to the order pool.
        """

        # Ensure order is not yet filled
        if order.filled > 0:
            raise CannotModifyFilledOrderError(
                "Cannot submit an order that has already been filled."
            )

        # Aside from emergency exits, we should not be able to submit any orders once a stop loss
        # has triggered
        if (
            self.in_cooldown
            and (order.order_type != OrderType.EMERGENCY_EXIT)
            and (order.order_type != OrderType.STOP_LOSS)
            and (order.order_type != OrderType.DANGLING_SHARES)
        ):
            raise StopLossCooldownIsActiveError(
                "Cannot submit an order while the stop loss is on cooldown."
            )

        # Handle any pre-check logic based on the order type
        if order.order_type == OrderType.ENTRY:
            # Ensure that the side matchees the position side
            if self.side == PositionSide.LONG and order.action != OrderAction.BUY:
                raise InvalidExecutionError("Cannot submit a BUY entry order for a SHORT position.")
            if self.side == PositionSide.SHORT and order.action != OrderAction.SELL:
                raise InvalidExecutionError("Cannot submit a SELL entry order for a LONG position.")

        if order.order_type == OrderType.EXIT:
            if not self.trade_to_close:
                raise InvalidExecutionError(
                    "Cannot submit an exit order without a trade that is ready to close."
                )

            # Ensure that the size of the exit order does not exceed the trade to close
            if order.size > self.trade_to_close.size:
                order.size = self.trade_to_close.size

            # Ensure that the action of the exit order is correct based on the side of the trade
            if self.trade_to_close.side == PositionSide.LONG and order.action != OrderAction.SELL:
                raise InvalidExecutionError(
                    "Cannot submit an exit order with a BUY action for a LONG trade."
                )
            if self.trade_to_close.side == PositionSide.SHORT and order.action != OrderAction.BUY:
                raise InvalidExecutionError(
                    "Cannot submit an exit order with a SELL action for a SHORT trade."
                )

            if (
                self.trade_to_close.exit_order
                and self.trade_to_close.exit_order.order_id != order.order_id
            ):
                raise InvalidExecutionError(
                    "Cannot submit an exit order with a different order ID than the trade to close."
                )

            # Make sure that the trade is makred with the exit
            self.trade_to_close.add_exit_order(order)

        if order.order_type == OrderType.DANGLING_SHARES:
            pass

        # Submit the order to the order pool appropriately
        match order.order_type:
            case OrderType.ENTRY:
                order = self.pool.submit_order(order)
            case OrderType.EXIT:
                if not parent_trade:
                    raise InvalidExecutionError(
                        "Cannot submit an EXIT order without a parent trade."
                    )
                order = self.pool.submit_order(order)
                parent_trade.add_exit_order(order)
            case OrderType.TAKE_PROFIT:
                if not parent_trade:
                    raise InvalidExecutionError(
                        "Cannot submit a TAKE_PROFIT order without a parent trade."
                    )
                order = self.pool.submit_order(order)
                parent_trade.set_take_profit_order(order)
            case OrderType.STOP_LOSS:
                if not parent_trade:
                    raise InvalidExecutionError(
                        "Cannot submit a STOP_LOSS order without a parent trade."
                    )
                order = self.pool.submit_order(order)
                parent_trade.set_stop_loss_order(order)
            case OrderType.EMERGENCY_EXIT:
                order = self.pool.submit_order(order)

            case OrderType.DANGLING_SHARES:
                order = self.pool.submit_order(order)

        return order

    # Event Handlers
    # ===============================
    def handle_position_update(
        self, ticker: str, contract_id: int, position: float, avg_price: float
    ) -> None:
        """
        Handle position updates from TWS.
        
        This method updates the TWS position tracking and triggers position limit checks
        when necessary, but does NOT automatically synchronize internal position to avoid
        creating feedback loops that inflate the internal position.
        """
        # Store original position for comparison
        original_internal_position = self.size
        original_true_share_count = self.true_share_count
        
        # Update TWS position data
        self._contract_id = contract_id
        self.true_share_count = int(position)
        
        logger.info(
            f"UPDATE [{self.assignment.ticker}] -- "
            f"TWS Share Count: {self.true_share_count} @ {avg_price} "
            f"(Internal size: {self.size})"
        )
        
        # Calculate position mismatch
        position_mismatch = abs(self.true_share_count - self.size)
        
        # CRITICAL FIX: Only log mismatches, don't automatically synchronize
        # Automatic synchronization was causing internal position inflation
        if position_mismatch > 0:
            logger.warning(
                f"POSITION MISMATCH [{self.assignment.ticker}] -- "
                f"TWS Position: {self.true_share_count}, Internal Position: {self.size}, "
                f"Mismatch: {position_mismatch} shares. "
                f"NOT auto-synchronizing to prevent position inflation."
            )
            
            # If TWS position exceeds limits, trigger position limit check to cancel pending orders
            if abs(self.true_share_count) > self.assignment.max_position_size:
                logger.warning(
                    f"[{self.assignment.ticker}] TWS position ({abs(self.true_share_count)}) exceeds max_position_size ({self.assignment.max_position_size}). "
                    f"Triggering position limit check to cancel pending orders."
                )
                
                # Trigger position limit check to cancel any pending entry orders
                if hasattr(self, '_position_limit_check_callback') and self._position_limit_check_callback:
                    logger.debug(f"[{self.assignment.ticker}] Triggering position limit check for oversized TWS position")
                    self._position_limit_check_callback()
            
            # Always trigger position limit check when there's a mismatch
            # This ensures position limits are enforced based on current state
            if hasattr(self, '_position_limit_check_callback') and self._position_limit_check_callback:
                logger.debug(f"[{self.assignment.ticker}] Triggering position limit check due to position mismatch")
                self._position_limit_check_callback()

        # If TWS reports position is now zero, ensure TP/SL orders in the pool are marked for cancellation.
        # We rely solely on true_share_count from TWS as the trigger.
        if self.true_share_count == 0 and original_true_share_count != 0: # Check if it just became zero
            logger.warning(
                f"TWS ZERO POS [{self.assignment.ticker}] -- Marking TP/SL orders in pool for cancellation."
            )
            
            orders_marked = 0
            # Iterate directly through the pool's current orders
            for order in list(self.pool.index.values()): # Use list copy for safe iteration if needed
                if order.order_type in [OrderType.TAKE_PROFIT, OrderType.STOP_LOSS]:
                    logger.debug(f"[{self.assignment.ticker}] Adding {order.order_type.value} order ID {order.order_id} to cancel list due to TWS zero pos.")
                    # Use set_cancel_status to avoid duplicates if called rapidly
                    self.set_cancel_status(order) 
                    orders_marked += 1
            
            if orders_marked > 0:
                 logger.info(f"[{self.assignment.ticker}] Marked {orders_marked} bracket orders for cancellation.")
            else:
                 logger.info(f"[{self.assignment.ticker}] TWS position is zero, but no active TP/SL orders found in pool to mark for cancellation.")
    
    def set_position_limit_check_callback(self, callback):
        """
        Set a callback function to be called when position synchronization occurs.
        This allows the OrderExecutor to trigger position limit checks after sync.
        """
        self._position_limit_check_callback = callback

    def handle_submitted(
        self, order_id: int, filled: float, avg_price: float
    ) -> Tuple[MongerOrder, list[MongerOrder]]:
        """
        Handle a submitted order event by ensuring it is updated in the pool.

        :param int order_id: The ID of the order that was submitted.
        :param float filled: The number of shares/contracts filled.
        :param float avg_price: The average price at which the order was filled.
        :return list[MongerOrder]: A list of orders that need to be cancelled.

        :raises InvalidExecutionError: If the order does not exist in the pool.
        """
        order = self.pool.handle_submitted(order_id, filled, avg_price)

        match order.order_type:
            case OrderType.ENTRY:
                # CRITICAL FIX: Do NOT call add_to_position here for partial fills
                # Only the FILLED handler should update position tracking to avoid double-counting
                # The SUBMITTED handler should only update the order state in the pool
                pass

            case OrderType.EXIT:
                # Ensure that we have a trade to close out
                if not self.trade_to_close:
                    raise InvalidExecutionError(
                        "Cannot submit an exit order without a trade that is ready to close."
                    )

                # Update the order in the pending pool and ensure it is associated with the trade
                self.trade_to_close.add_exit_order(order)

                # CRITICAL FIX: Do NOT call remove_from_position here for partial fills
                # Only the FILLED handler should update position tracking to avoid double-counting
                pass

            case OrderType.TAKE_PROFIT:
                # CRITICAL FIX: Do NOT call remove_from_position here for partial fills
                # Only the FILLED handler should update position tracking to avoid double-counting
                pass

            case OrderType.STOP_LOSS:
                # NOTE: We want to fire the cool trigger here, since this will event will
                # be recieved as soon as the auxPrice is hit (even if no shares have filled)
                self.cooldown_trigger()
                # CRITICAL FIX: Do NOT call remove_from_position here for partial fills
                # Only the FILLED handler should update position tracking to avoid double-counting
                pass

            case OrderType.EMERGENCY_EXIT:
                # CRITICAL FIX: Do NOT call remove_from_position here for partial fills
                # Only the FILLED handler should update position tracking to avoid double-counting
                pass
            case OrderType.DANGLING_SHARES:
                pass
            case _:
                raise ValueError(f"Invalid order type: {order.order_type}")

        return order

    def handle_cancelled(
        self, order_id: int, filled: float, avg_price: float
    ) -> Tuple[MongerOrder, list[MongerOrder]]:
        """
        Handle an order being cancelled.

        NOTE: Currently the match block is arbitrary, but we may eventually have more cancellation
        callback logic in the future, so it is good to get this boilerplate in place.

        :param int order_id: The ID of the order that was cancelled.
        :param float filled: The number of shares/contracts filled.
        :param float avg_price: The average price at which the order was filled.
        :return list[MongerOrder]: A list of the orders that must be cancelled.
        """
        cancelled_order = self.pool.handle_cancelled(order_id, filled, avg_price)

        match cancelled_order.order_type:
            case OrderType.ENTRY:
                # CRITICAL FIX: Track partial fills from cancelled ENTRY orders
                if filled > 0:
                    self.add_to_position(order_id, cancelled_order.action, filled, avg_price)

            case OrderType.EXIT:
                # We need to remove the exit order from the position
                if filled > 0:
                    self.remove_from_position(order_id, filled, avg_price)
                    self.trade_to_close.close_exit_order()

            case OrderType.TAKE_PROFIT:
                if filled > 0:
                    self.remove_from_position(order_id, filled, avg_price, is_take_profit=True)

                relevant_trade = self.get_trade_by_take_profit_order_id(order_id)
                if relevant_trade:
                    relevant_trade.take_profit_order = None

            case OrderType.STOP_LOSS:
                if filled > 0:
                    self.remove_from_position(order_id, filled, avg_price, is_stop_loss=True)

                relevant_trade = self.get_trade_by_stop_loss_order_id(order_id)
                if relevant_trade:
                    relevant_trade.stop_loss_order = None

            case OrderType.EMERGENCY_EXIT:
                if filled > 0:
                    self.remove_from_position(order_id, filled, avg_price)
            case OrderType.DANGLING_SHARES:
                self.pool.remove(order_id)
            case _:
                raise ValueError(f"Invalid order type: {cancelled_order.order_type}")

        return cancelled_order

    def handle_filled(self, order_id: int, filled: float, avg_price: float) -> MongerOrder:
        """
        Handle an order being filled.

        :param int order_id: The ID of the order that was filled.
        :param float filled: The number of shares/contracts filled.
        :param float avg_price: The average price at which the order was filled.
        :return list[MongerOrder]: A list of the orders that must be cancelled.
        """
        filled_order = self.pool.handle_filled(order_id, filled, avg_price)

        match filled_order.order_type:
            case OrderType.ENTRY:
                # Update or add a trade
                self.add_to_position(order_id, filled_order.action, filled, avg_price)

            case OrderType.EXIT:
                # Remove shares from the position
                self.remove_from_position(order_id, filled, avg_price)

            case OrderType.TAKE_PROFIT:
                # Remove filled shares and mark trade as take profited
                self.remove_from_position(order_id, filled, avg_price, is_take_profit=True)

                # Make sure that the Take Profit is removed from the trade
                relevant_trade = self.get_trade_by_take_profit_order_id(order_id)
                if relevant_trade:
                    relevant_trade.take_profit_order = None

            case OrderType.STOP_LOSS:
                # Remove filled shares and mark trade as stop loss hit
                self.remove_from_position(order_id, filled, avg_price, is_stop_loss=True)

                # Update the stop loss order fill event, to prevent re-entry for 60 seconds
                self.cooldown_trigger()

                # Make sure that the Stop Loss is removed from the trade
                relevant_trade = self.get_trade_by_stop_loss_order_id(order_id)
                if relevant_trade:
                    relevant_trade.stop_loss_order = None

            case OrderType.EMERGENCY_EXIT:
                # Remove all filled shares from the position
                self.remove_from_position(order_id, filled, avg_price)

            case OrderType.DANGLING_SHARES:
                self.pool.remove(order_id)
            case _:
                raise ValueError(f"Invalid order type: {filled_order.order_type}")

        return filled_order
