from abc import ABC, abstractmethod, abstractproperty
from decimal import Decimal
import threading
from typing import TYPE_CHECKING, Optional, Union, Any
from datetime import datetime, timedelta

from ibapi.contract import Contract as IBContract
from loguru import logger

from src.error import (
    CannotModifyFilledOrderError,
    InvalidExecutionError,
    OrderDoesNotExistError,
    StopLossCooldownIsActiveError,
)
from schema import MarketData, MongerOrder, Position, Prediction, Trade
from schema.assignment import TraderAssignment
from schema.enums import OrderAction, OrderStatus, OrderType
from schema.prediction import PriceDirection

from .order_queue import OrderQueue, queued_execution
from .predicate import ALL_PREDICATES, ConfidenceThresholdPredicate, PositionSizePredicate


if TYPE_CHECKING:
    from app import TradeMonger


class OrderIdManager:
    """
    A simple wrapper around the TWS API's nextOrderId method to ensure that order IDs are unique.

    :param tws_app: The TWS application instance.
    """

    def __init__(self, tws_app: "TradeMonger"):
        self.tws_app = tws_app
        self._next_id = None
        self._lock = threading.Lock()

    def next_id(self):
        """
        Get the next order ID.
        """
        with self._lock:
            if self._next_id is None:
                self._next_id = self.tws_app.nextOrderId()
            next_id = self._next_id
            self._next_id += 1
            return next_id


class AbstractOrderExecutorMixin(ABC):
    """
    An abstract mixin for handling order execution.
    """

    position: Position
    assignment: TraderAssignment
    market_data: MarketData

    initialized: bool
    is_emergency_exit: bool
    tws_app: Optional["TradeMonger"]
    portfolio_manager: Optional[Any]

    @abstractproperty
    def contract(self) -> IBContract:
        return None

    @abstractmethod
    def place_order(self, monger_order: MongerOrder):
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order: MongerOrder):
        raise NotImplementedError

    @abstractmethod
    def modify_order(self, order: MongerOrder):
        raise NotImplementedError

    @abstractmethod
    def _place_take_profit(self):
        raise NotImplementedError

    @abstractmethod
    def _place_stop_loss(self):
        raise NotImplementedError

    @abstractmethod
    def _emergency_exit_protocol(self):
        raise NotImplementedError


class OrderStatusMixin(AbstractOrderExecutorMixin):
    """
    A mixin for handling order status updates from TWS.
    """

    @queued_execution
    def handle_order_status(
        self,
        order_id: int,
        status: str,
        filled: Union[float, Decimal],
        avg_fill_price: Union[float, Decimal],
        client_id: int,
    ):
        """
        Handle order status updates from TWS.

        :param int order_id: The order ID.
        :param str status: The order status.
        :param Union[float, Decimal] filled: The filled size.
        :param Union[float, Decimal] avg_fill_price: The average fill price.
        """

        # Gaurd to ensure that filled and avg_fill_price are float values
        filled = float(filled)
        avg_fill_price = float(avg_fill_price)

        match status:
            case OrderStatus.PRE_SUBMITTED.value:
                self._handle_order_pre_submitted(order_id, filled, avg_fill_price, client_id)
            case OrderStatus.SUBMITTED.value:
                self._handle_order_submitted(order_id, filled, avg_fill_price, client_id)
            case OrderStatus.FILLED.value:
                self._handle_order_filled(order_id, filled, avg_fill_price, client_id)
            case OrderStatus.CANCELED.value:
                self._handle_order_canceled(order_id, filled, avg_fill_price, client_id)
            case _:
                try:
                    order = self.position.pool[order_id]
                    logger.debug(
                        f"ORDER STATUS -- Order ID {order_id} ({order.order_type}): {status}"
                    )
                except KeyError:
                    logger.debug(
                        f"ORDER STATUS -- Order ID {order_id} (UNKOWN): not found in position pool."
                    )

        # Make sure that we cancel any orders that must be cancelled as a result of the recent
        # status update
        for order_to_cancel in self.position.orders_to_cancel:
            self.cancel_order(order_to_cancel)

        # Clear the list after processing to prevent duplicates
        self.position.orders_to_cancel.clear()

    def _handle_order_pre_submitted(
        self, order_id: int, filled: float, avg_fill_price: float, client_id: int
    ):
        """
        Handle an order being pre-submitted.

        :param int order_id: The order ID.
        :param float filled: The filled size.
        :param float avg_fill_price: The average fill price.
        """
        # We only need to do things for stop loss orders here
        try:
            submitted_order = self.position.pool[order_id]
        except OrderDoesNotExistError:
            logger.warning(
                f"PRE-SUBMITTED [{self.assignment.ticker}] -- "
                f"Order ID {order_id} not found in position pool."
            )  # noqa: E501
            return

        logger.info(
            f"PRE-SUBMITTED [{self.assignment.ticker}] {submitted_order.order_type} -- Order ID {order_id}: "  # noqa: E501
            f"({submitted_order.order_type}: {submitted_order.size} X ${submitted_order.limit_price}) "  # noqa: E501
            f"Status: {filled} @ ${avg_fill_price}"
        )
        match submitted_order.order_type:
            case OrderType.STOP_LOSS:
                # Check if the position size is 0 and if it is, then cancel the order
                if self.position.size == 0:
                    logger.warning(
                        f"Position size is 0, cancelling excess STOP LOSS "
                        f"order {submitted_order.order_id}"
                    )
                    self.cancel_order(submitted_order)
            case _:
                return

    def _handle_order_submitted(
        self, order_id: int, filled: float, avg_fill_price: float, client_id: int
    ):
        """
        Handle an order being submitted.

        :param int order_id: The order ID.
        :param float filled: The filled size.
        :param float avg_fill_price: The average fill price.
        """
        try:
            submitted_order = self.position.handle_submitted(order_id, filled, avg_fill_price)
        except OrderDoesNotExistError:
            logger.warning(
                f"SUBMITTED [{self.assignment.ticker}] -- "
                f"Order ID {order_id} not found in position pool."
            )
            return

        logger.info(
            f"SUBMITTED [{self.assignment.ticker}] {submitted_order.order_type} -- Order ID {order_id}: "  # noqa: E501
            f"({submitted_order.order_type}: {submitted_order.size} X ${submitted_order.limit_price}) "  # noqa: E501
            f"Status: {filled} @ ${avg_fill_price}"
        )
        match submitted_order.order_type:
            case OrderType.ENTRY:
                if filled > 0:
                    self._place_stop_loss()
                    self._place_take_profit()
                    # CRITICAL FIX: Check position size after entry fills
                    self.handle_max_position_size_check()

            case OrderType.EXIT:
                if filled > 0:
                    self._place_stop_loss()
                    self._place_take_profit()

            case OrderType.TAKE_PROFIT:
                if filled > 0:
                    self._place_stop_loss()

            # This means that the stop loss price was triggered
            case OrderType.STOP_LOSS:
                if filled > 0:
                    self._place_take_profit()

            case OrderType.EMERGENCY_EXIT:
                pass

    def _handle_order_filled(
        self, order_id: int, filled: float, avg_fill_price: float, client_id: int
    ):
        """
        Handle an order being filled.

        :param int order_id: The order ID.
        :param float filled: The filled size.
        :param float avg_fill_price: The average fill price.
        """
        # --- Get order details BEFORE updating position state --- 
        try:
            order_in_pool = self.position.pool[order_id]
            original_order_type = order_in_pool.order_type
            original_size = order_in_pool.size
            original_limit_price = order_in_pool.limit_price
        except OrderDoesNotExistError:
            logger.debug(
                f"FILLED DUPLICATE [{self.assignment.ticker}] -- "
                f"Order ID {order_id} not found in position pool. Likely already processed."
            )
            return
        # --- End Get order details ---

        try:
            # This call updates Position state and removes the order from the pool
            filled_order = self.position.handle_filled(order_id, filled, avg_fill_price)
        except OrderDoesNotExistError:
            # This case indicates the order was already processed between the pre-check and this call
            logger.debug(
                f"FILLED RACE CONDITION [{self.assignment.ticker}] -- "
                f"Order ID {order_id} was removed from pool between pre-check and handle_filled. Already processed."
            )
            return

        # Log using the retrieved original details, as filled_order might have changed state
        logger.info(
            f"FILLED [{self.assignment.ticker}] {original_order_type} -- Order ID {order_id}: "
            f"({original_order_type}: {original_size} X ${original_limit_price}) " # Use original details for log
            f"Status: {filled} @ ${avg_fill_price}"
        )
        
        # --- Use the original order type for the match statement ---
        match original_order_type:
            case OrderType.ENTRY:
                self._place_stop_loss()
                self._place_take_profit()

            case OrderType.EXIT:
                # If an exit order fills, cancel all associated TP/SL orders
                logger.info(f"EXIT FILLED [{self.assignment.ticker}] -- Cancelling associated bracket orders.")
                for sl_order in self.position.stop_loss_orders:
                    self.cancel_order(sl_order)
                for tp_order in self.position.take_profit_orders:
                    self.cancel_order(tp_order)

            case OrderType.TAKE_PROFIT:
                # If TP fills (partially or fully), adjust/replace the SL
                self._place_stop_loss()

            case OrderType.STOP_LOSS:
                # If SL fills (partially or fully), adjust/replace the TP
                self._place_take_profit()

            case OrderType.EMERGENCY_EXIT:
                # If Emergency Exit fills, ensure all other orders are cancelled (redundancy)
                logger.info(f"EMERGENCY EXIT FILLED [{self.assignment.ticker}] -- Cancelling any remaining bracket orders.")
                for sl_order in self.position.stop_loss_orders:
                    self.cancel_order(sl_order)
                for tp_order in self.position.take_profit_orders:
                    self.cancel_order(tp_order)
                
                # Only set emergency exit to False if position is FULLY closed
                if self.position.true_share_count == 0:
                    logger.info(f"[{self.assignment.ticker}] Position fully closed after emergency exit fill, ending emergency exit mode")
                    self.is_emergency_exit = False
                else:
                    logger.warning(f"[{self.assignment.ticker}] Emergency exit order filled but position still has {self.position.true_share_count} shares remaining")
                    # Keep is_emergency_exit = True so retry loop continues

            case OrderType.DANGLING_SHARES:
                 # If a dangling shares order fills and the *internal* position is now zero,
                 # cancel remaining brackets. The TWS update check provides redundancy.
                 if self.position.size == 0:
                     logger.info(f"DANGLING SHARES FILLED & INTERNAL POS ZERO [{self.assignment.ticker}] -- Cancelling any remaining bracket orders.")
                     for sl_order in self.position.stop_loss_orders:
                         self.cancel_order(sl_order)
                     for tp_order in self.position.take_profit_orders:
                         self.cancel_order(tp_order)
            
            case _: # Should not happen if order was found in pool initially
                 logger.error(f"Unhandled filled order type in OrderExecutor: {original_order_type}")

    def _handle_order_canceled(
        self, order_id: int, filled: float, avg_fill_price: float, client_id: int
    ):
        """
        Handle an order being canceled.

        :param int order_id: The order ID.
        :param float filled: The filled size.
        :param float avg_fill_price: The average fill price.
        """
        try:
            cancelled_order = self.position.handle_cancelled(order_id, filled, avg_fill_price)
        except OrderDoesNotExistError:
            logger.warning(
                f"CANCELED [{self.assignment.ticker}] -- "
                f"Order ID {order_id} not found in position pool."
            )
            return

        logger.info(
            f"CANCELLED [{self.assignment.ticker}] {cancelled_order.order_type} -- Order ID {order_id}: "  # noqa: E501
            f"({cancelled_order.order_type}: {cancelled_order.size} X ${cancelled_order.limit_price}) "  # noqa: E501
            f"Status: {filled} @ ${avg_fill_price}"
        )
        match cancelled_order.order_type:
            # If we have cancelled an exit order, we want to make sure that we
            # replace the order with a new one
            case OrderType.EXIT:
                self.position.trade_to_close.close_exit_order()
                self.handle_expired_positions()

            case OrderType.TAKE_PROFIT:
                self._place_stop_loss()

            case OrderType.STOP_LOSS:
                self._place_take_profit()

            # If we cancel an emergency exit, we want to make sure that we
            # replace the order with a new one
            case OrderType.EMERGENCY_EXIT:
                self._emergency_exit_protocol()
            
            case OrderType.DANGLING_SHARES:
                 logger.info(f"[{self.assignment.ticker}] Processing CANCELED status for DANGLING_SHARES order ID {order_id}.")
                 # The actual removal from pool happens in position.handle_cancelled
                 pass 


class MarketDataMixin(AbstractOrderExecutorMixin):
    """
    A mixin for handling market data updates from TWS.
    """

    @queued_execution
    def handle_market_data_update(self):
        """
        Handle market data updates.
        """
        # If we have no position, we don't do anything
        if not self.position.side:
            return

        # If we have a position, we need to ensure that our stop loss order is up to date
        self._place_stop_loss()
        self._place_take_profit()


class TraderMixin(AbstractOrderExecutorMixin):
    """
    A mixin for handling updates from the trader.
    """

    def __init__(self):
        self._predicates = {
            predicate: predicate(assignment=self.assignment) for predicate in ALL_PREDICATES
        }
        self.consecutive_dangling_shares_flags = 0

        return self

    def check_boolean_entry_predicate(self, prediction: Prediction) -> bool:
        """
        Check if the entry predicates are satisfied.

        :param Prediction prediction: The prediction from the inference model.
        :return bool: True if the entry predicates are satisfied, False otherwise.
        """
        # Remove confidence predicate check
        # confidence_predicate = self._predicates[ConfidenceThresholdPredicate]
        max_position_size_predicate = self._predicates[PositionSizePredicate]

        # confidence_predicate_result = confidence_predicate.apply(prediction)
        max_position_size_predicate_result = max_position_size_predicate.apply(self.position)
        is_in_cooldown = self.position.in_cooldown

        # Log remaining predicate results
        logger.debug(
            f"[{self.assignment.ticker}] Entry Predicates Check: "
            # f"ConfidenceOK={confidence_predicate_result}, " # Removed
            f"MaxSizeOK={max_position_size_predicate_result}, "
            f"InCooldown={is_in_cooldown}"
        )

        # Update result logic to exclude confidence check
        result = (
            # confidence_predicate_result and # Removed
            max_position_size_predicate_result
            and not is_in_cooldown
        )
        logger.debug(f"[{self.assignment.ticker}] Final Predicate Result: {result}")
        return result

    def check_position_scaling_predicate(self) -> float:
        """
        Check the position scaling predicate.

        :return float: The scaling factor for the position size.
        """
        return 1.0

    @queued_execution
    def handle_prediction(self, prediction: Prediction):
        """
        Handle the prediction and execute the order if the conditions are favorable.
        
        This method is called with staggered delays to ensure orders are placed
        with current market prices, not stale prices from when signals were queued.

        :param Prediction prediction: The prediction from the inference model.
        """
        logger.debug(f"[{self.assignment.ticker}] Handling prediction: Flag={prediction.flag}, Conf={getattr(prediction, 'confidence', 'N/A')}")
        
        # Re-check predicates at the time of actual order placement
        # This ensures we're using current position size, not stale data
        predicates_passed = self.check_boolean_entry_predicate(prediction)
        logger.debug(f"[{self.assignment.ticker}] Predicates passed: {predicates_passed}")

        if predicates_passed:
            # CRITICAL FIX: Check if we're at the max position limit before placing any orders
            # Use TWS position (true_share_count) instead of internal position (size) for consistency
            current_tws_position = abs(self.position.true_share_count)
            if current_tws_position >= self.assignment.max_position_size:
                logger.info(
                    f"[{self.assignment.ticker}] Position at maximum limit. "
                    f"Current TWS Position: {current_tws_position}, Max: {self.assignment.max_position_size}. "
                    f"Skipping order placement."
                )
                return

            # Calculate position size based on CURRENT TWS position state
            max_position_size = self.assignment.max_position_size
            delta_to_max = max_position_size - current_tws_position
            position_size = min(delta_to_max, self.assignment.position_size)

            if position_size <= 0:
                logger.debug(f"[{self.assignment.ticker}] Position size is 0, skipping order placement.")
                return

            # Get FRESH market data at the time of actual order placement
            # This is critical for fast-moving stocks where price changes rapidly
            best_spread = self.assignment.spread_strategy.upper() == "BEST"
            entry_price = self.assignment.spread_strategy.upper() == "BEST"
            entry_price = self.market_data.order_book.get_entry_price(prediction.flag, best_spread)
            
            logger.info(f"[{self.assignment.ticker}] FRESH ORDER PLACEMENT: Size={position_size}, Price=${entry_price:.2f}, Strategy={self.assignment.spread_strategy}, Current TWS Position={current_tws_position}/{max_position_size}")

            order_action = (
                OrderAction.BUY if prediction.flag == PriceDirection.BULLISH else OrderAction.SELL
            )
            self.place_order(
                order_type=OrderType.ENTRY,
                order_action=order_action,
                size=position_size,
                price=entry_price,
            )

    @queued_execution
    def handle_expired_positions(self):
        """
        Handle expired positions.
        """

        try:
            if expired_trade := self.position.trade_to_close:
                if self.position.in_cooldown:
                    return

                # If there is a current exit order, then we don't need to place a new one
                if self.position.trade_to_close.exit_order:
                    return

                # Determine spread strategy: BEST = True, WORST = False
                best_spread = self.assignment.spread_strategy.upper() == "BEST"
                exit_price = self.market_data.order_book.get_exit_price(self.position.side, best_spread)

                self.place_order(
                    order_type=OrderType.EXIT,
                    order_action=self.position.exit_action,
                    size=expired_trade.size,
                    price=exit_price,
                    parent_trade=expired_trade,
                )
        except InvalidExecutionError as e:
            logger.warning(e)

    @queued_execution
    def handle_dangling_shares(self):
        """
        Simplified dangling shares handler - only enforces position limits.
        
        We don't try to "fix" position mismatches. We only care that TWS position
        doesn't exceed max_position_size. If it does, we cancel pending entry orders.
        """
        tws_position = abs(self.position.true_share_count)
        max_allowed = self.assignment.max_position_size
        
        # Log mismatch for monitoring purposes only
        if self.position.relevant_position_size != self.position.true_share_count:
            logger.warning(
                f"POSITION MISMATCH [{self.assignment.ticker}] -- "
                f"TWS: {self.position.true_share_count}, "
                f"Internal: {self.position.relevant_position_size} "
                f"(Difference: {self.position.true_share_count - self.position.relevant_position_size})"
            )
        
        # ONLY action: Cancel entry orders if TWS position exceeds limit
        if tws_position > max_allowed:
            logger.critical(
                f"POSITION LIMIT EXCEEDED [{self.assignment.ticker}] -- "
                f"TWS Position: {tws_position} > Max: {max_allowed}. "
                f"Cancelling all pending entry orders."
            )
            
            # Cancel all pending entry orders
            pending_entries = [
                order for order in self.position.pool.orders 
                if order.order_type == OrderType.ENTRY
            ]
            
            for entry_order in pending_entries:
                logger.warning(f"[{self.assignment.ticker}] Cancelling entry order {entry_order.order_id}")
                self.cancel_order(entry_order)
        
        # Reset the consecutive flags since we're not doing complex protocols
        self.consecutive_dangling_shares_flags = 0

    @queued_execution
    def handle_emergency_exit(self, final: bool = False):
        """
        Handle emergency exit.
        """
        logger.warning(f"[{self.assignment.ticker}] -- Handling emergency exit")
        self._emergency_exit_protocol(final=final)

    @queued_execution
    def handle_pnl_checks(self) -> None:
        # Check if the realized P/L values of any trades have exceeded the threshold
        if self.position.realized_pnls:
            min_pnl = min(self.position.realized_pnls)
            if min_pnl < (-1 * self.assignment.max_loss_per_trade):
                self.execute_emergency_exit()

    @queued_execution
    def handle_pnl_update(self, realized_pnl: float, unrealized_pnl: float) -> None:
        # Check if the realized P/L has exceeded the clip threshold
        return
        # TODO: Removed out clipping for now as it was causing bugs
        # total_pnl = realized_pnl + unrealized_pnl

        # if realized_pnl > self.assignment.clip_activation and not self.clip_active:
        #     logger.success(
        #         f"[{self.assignment.ticker}] Activating Clip Level: "
        #         f"${self.assignment.clip_activation}"
        #     )
        #     self.clip_active = True

        # # If the clip is active, check if the total P/L has fallen below the threshold
        # if self.clip_active and total_pnl < self.assignment.clip_stop_loss:
        #     logger.warning(
        #         f"[{self.assignment.ticker}] Clip stop loss level met: "
        #         f"({self.assignment.clip_stop_loss}). Exiting position."
        #     )
        #     self.execute_emergency_exit()

    @queued_execution
    def handle_max_position_size_check(self) -> None:
        """
        Check if the current TWS position size exceeds the maximum allowed size.
        If it does, cancel any pending entry orders.
        """
        tws_size = abs(self.position.true_share_count)
        max_size = self.assignment.max_position_size
        
        logger.debug(f"[{self.assignment.ticker}] Position size check: TWS={tws_size}, Max={max_size}")
        
        # Check if TWS position exceeds limits and cancel pending entry orders
        if tws_size >= max_size:
            # Find all pending entry orders
            pending_entry_orders = [
                order for order in self.position.pool.orders 
                if order.order_type == OrderType.ENTRY
            ]
            
            if pending_entry_orders:
                logger.warning(
                    f"[{self.assignment.ticker}] MAX POSITION SIZE REACHED! "
                    f"TWS Position: {tws_size}, Max: {max_size}. "
                    f"Cancelling {len(pending_entry_orders)} pending entry orders."
                )
                
                for entry_order in pending_entry_orders:
                    logger.info(f"[{self.assignment.ticker}] Cancelling entry order {entry_order.order_id} ({entry_order.size} shares @ ${entry_order.limit_price})")
                    self.cancel_order(entry_order)
            else:
                logger.info(f"[{self.assignment.ticker}] TWS position at/above max ({tws_size}/{max_size}) but no pending entry orders to cancel.")
        else:
            logger.debug(f"[{self.assignment.ticker}] Position within limits: {tws_size}/{max_size}")


class OrderExecutor(OrderStatusMixin, MarketDataMixin, TraderMixin):
    def __init__(
        self,
        position: Position,
        assignment: TraderAssignment,
        market_data: MarketData,
        tws_app: Optional["TradeMonger"] = None,
        portfolio_manager: Optional[Any] = None,
        staggered_order_delay: float = 5.0
    ):
        self.assignment = assignment
        self.position = position
        self.market_data = market_data
        self.order_queue = OrderQueue(staggered_order_delay=staggered_order_delay)

        # Initialize Mixins
        super(OrderStatusMixin, self).__init__()
        super(MarketDataMixin, self).__init__()
        super(TraderMixin, self).__init__()

        self.initialized = False
        self.is_emergency_exit = False
        self.clip_active = False
        self.portfolio_manager = portfolio_manager

        # Pass portfolio_manager in context when creating Position
        context = {'portfolio_manager': self.portfolio_manager}
        self.position = Position(assignment=assignment, model_config={"arbitrary_types_allowed": True}, **context)

    @property
    def contract(self) -> IBContract:
        """
        Return the contract for the current position.

        :return IBContract: The IBContract instance.
        """
        contract = IBContract()
        contract.conId = self.position.contract_id
        contract.symbol = self.assignment.ticker
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"
        return contract

    def initialize(self, app: "TradeMonger") -> None:
        """
        Initialize the order executor with the TradeMonger application instance.

        :param app: The TradeMonger application instance.
        """
        self.tws_app = app
        self.order_id_manager = OrderIdManager(app)
        self.initialized = True
        self.order_queue.start()
        
        # Set position limit check callback for position synchronization
        self.position.set_position_limit_check_callback(self.handle_max_position_size_check)

    def shutdown(self):
        """
        Shutdown the order executor.
        """
        try:
            self.order_queue.stop()
        except RuntimeError:
            pass

    def execute_emergency_exit(self, final: bool = False) -> None:
        """
        Execute the emergency exit protocol, setting the flag before placing the task in the queue.
        """
        if not final:
            self.is_emergency_exit = True
        self.handle_emergency_exit(final=final)

    def place_order(
        self,
        order_type: OrderType,
        order_action: OrderAction,
        size: float,
        price: float,
        parent_trade: Optional[Trade] = None,
    ) -> int:
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before placing orders")

        if self.is_emergency_exit and order_type != OrderType.EMERGENCY_EXIT:
            return

        # CRITICAL FIX: Check TWS position size limit before placing any entry orders
        if order_type == OrderType.ENTRY and order_action == OrderAction.BUY:
            current_tws_position = abs(self.position.true_share_count)
            projected_tws_position = current_tws_position + size
            
            if projected_tws_position > self.assignment.max_position_size:
                logger.warning(
                    f"[{self.assignment.ticker}] ORDER BLOCKED - TWS position size limit would be exceeded! "
                    f"Current TWS: {current_tws_position}, Order size: {size}, "
                    f"Projected TWS: {projected_tws_position}, Max: {self.assignment.max_position_size}. "
                    f"Skipping order placement."
                )
                return
            
            if current_tws_position >= self.assignment.max_position_size:
                logger.warning(
                    f"[{self.assignment.ticker}] ORDER BLOCKED - TWS position at/above limit! "
                    f"TWS position: {current_tws_position}, Max: {self.assignment.max_position_size}. "
                    f"Skipping order placement."
                )
                return

        order_id = self.order_id_manager.next_id()
        order = MongerOrder(
            order_id=order_id,
            order_type=order_type,
            action=order_action,
            size=size,
            limit_price=price,
        )

        # Verify the order and submit it to our pool -- shoudl catch exceptions here
        try:
            self.position.submit_order(order, parent_trade=parent_trade)
        except InvalidExecutionError as e:
            logger.warning(f"Order {order_id} is invalid: {e}")
            return
        ib_order = order.ib_order
        logger.debug(
            f"[{self.assignment.ticker}] -- Placing {order.order_type.value} order with id: "
            f"{order_id}"
        )
        # --- DEBUG LOG ADDED ---
        logger.trace(f"[{self.assignment.ticker}] Submitting IBOrder Attributes: Action={ib_order.action}, Type={ib_order.orderType}, Qty={ib_order.totalQuantity}, LmtPx={ib_order.lmtPrice}, AuxPx={getattr(ib_order, 'auxPrice', 'N/A')}, TIF={ib_order.tif}, GTD={getattr(ib_order, 'goodTillDate', 'N/A')}, OTH={ib_order.outsideRth}, Transmit={ib_order.transmit}, ETradeOnly={getattr(ib_order, 'etradeOnly', 'N/A')}")
        # --- END DEBUG LOG ---
        self.tws_app.placeOrder(order_id, self.contract, ib_order)
        return order_id

    def cancel_order(self, order: MongerOrder) -> None:
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before canceling orders")

        # Check for the order in the pool -- if it has already been cancelled, than it will not
        # be in the pool
        try:
            _ = self.position.pool[order.order_id] # Just check existence
        except OrderDoesNotExistError:
            logger.debug(f"[{self.assignment.ticker}] Order {order.order_id} already removed from pool, skipping cancel call.")
            return

        # Log the explicit cancel call
        logger.debug(
            f"[{self.assignment.ticker}] -- EXECUTING cancelOrder for {order.order_type.value} order with id: "
            f"{order.order_id}"
        )
        # Workaround for potential ibapi 9.81.1 bug: Pass a dummy object with the expected attribute
        class DummyCancelTime:
            manualOrderCancelTime = ""
            extOperator = ""
            externalUserId = ""
            manualOrderIndicator = ""
        self.tws_app.cancelOrder(order.order_id, DummyCancelTime())

    def modify_order(self, order: MongerOrder, parent_trade: Optional[Trade] = None) -> None:
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before modifying orders")

        # Verify the order and submit it to our pool -- shoudl catch exceptions here
        self.position.submit_order(order, parent_trade=parent_trade)
        ib_order = order.ib_order
        assert order.order_id is not None, "A valid order ID is required to modify an order."

        logger.debug(
            f"[{self.assignment.ticker}] -- Modifying {order.order_type.value} order with id: "
            f"{order.order_id}"
        )
        self.tws_app.placeOrder(order.order_id, self.contract, ib_order)

    def _place_take_profit(self) -> None:
        """
        Place take profit orders for each trade that needs one.
        """
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before placing orders")

        for trade in self.position.trades.values():
            if trade.size == 0:
                continue

            take_profit_size = trade.get_take_profit_size()
            take_profit_price = trade.get_take_profit_price()

            if trade.take_profit_order:
                if take_profit_price is None or take_profit_size is None or self.is_emergency_exit:
                    self.cancel_order(trade.take_profit_order)
                    trade.clear_take_profit_order()
                elif trade.take_profit_order.requires_update(take_profit_size, take_profit_price):
                    trade.take_profit_order.size = take_profit_size
                    trade.take_profit_order.limit_price = take_profit_price
                    try:
                        self.modify_order(trade.take_profit_order, parent_trade=trade)
                    except CannotModifyFilledOrderError as e:
                        logger.warning(
                            f"Cannot modify TAKE PROFIT order for trade {trade.trade_id}: {str(e)}"
                        )
                        continue
                    except StopLossCooldownIsActiveError:
                        self.cancel_order(trade.take_profit_order)
                        continue
            else:
                if take_profit_price and take_profit_size:
                    try:
                        self.place_order(
                            order_type=OrderType.TAKE_PROFIT,
                            order_action=self.position.exit_action,
                            size=take_profit_size,
                            price=take_profit_price,
                            parent_trade=trade,
                        )
                    except StopLossCooldownIsActiveError:
                        logger.warning(
                            f"[{self.assignment.ticker}] -- Skipping placement of take profit "
                            "order as stop loss cooldown is active."
                        )

    def _place_stop_loss(self) -> None:
        """
        Place stop loss orders for each trade that needs one.
        """
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before placing orders")

        for trade in self.position.trades.values():
            if trade.size == 0:
                continue

            stop_loss_size = trade.get_stop_loss_size()
            stop_loss_price = trade.get_stop_loss_price(self.market_data)

            if trade.stop_loss_order:
                if stop_loss_price is None or stop_loss_size is None or self.is_emergency_exit:
                    self.cancel_order(trade.stop_loss_order)
                    trade.clear_stop_loss_order()
                elif trade.stop_loss_order.requires_update(stop_loss_size, stop_loss_price):
                    trade.stop_loss_order.size = stop_loss_size
                    trade.stop_loss_order.limit_price = stop_loss_price

                    if self.position.in_cooldown:
                        self.cancel_order(trade.stop_loss_order)
                        trade.clear_stop_loss_order()
                    else:
                        try:
                            self.modify_order(trade.stop_loss_order, parent_trade=trade)
                        except (CannotModifyFilledOrderError, StopLossCooldownIsActiveError) as e:
                            logger.warning(
                                f"Cannot modify STOP LOSS order for trade {trade.trade_id}: "
                                f"{str(e)}"
                            )
                            continue

            if stop_loss_price and stop_loss_size and not trade.stop_loss_order:
                # CRITICAL FIX: Check if there's already a pending stop loss order in the pool
                # This prevents race conditions where multiple market data updates queue
                # stop loss placements before the first one completes and sets trade.stop_loss_order
                pending_stop_loss = None
                for order in self.position.pool.orders:
                    if order.order_type == OrderType.STOP_LOSS:
                        # Check if this stop loss order belongs to the current trade
                        # by finding the trade that has this order as its stop_loss_order
                        for other_trade in self.position.trades.values():
                            if other_trade.stop_loss_order and other_trade.stop_loss_order.order_id == order.order_id:
                                if other_trade.trade_id == trade.trade_id:
                                    pending_stop_loss = order
                                    break
                        if pending_stop_loss:
                            break
                
                if pending_stop_loss:
                    logger.debug(f"[{self.assignment.ticker}] Stop loss order {pending_stop_loss.order_id} already pending for trade {trade.trade_id}, skipping duplicate")
                    continue
                    
                self.place_order(
                    order_type=OrderType.STOP_LOSS,
                    order_action=self.position.exit_action,
                    size=stop_loss_size,
                    price=stop_loss_price,
                    parent_trade=trade,
                )

    def _emergency_exit_protocol(self, final: bool = False) -> None:
        """
        Cancel all open orders and start aggressive emergency exit retry mechanism.
        """
        if not self.initialized:
            raise RuntimeError("OrderExecutor must be initialized before placing orders")

        # Add thread-safety to prevent multiple simultaneous calls
        if hasattr(self, '_emergency_exit_lock'):
            if self._emergency_exit_lock:
                logger.debug(f"[{self.assignment.ticker}] Emergency exit protocol already running, skipping duplicate call")
                return
        else:
            self._emergency_exit_lock = False

        self._emergency_exit_lock = True

        try:
            # CRITICAL FIX: Don't disable tws_app.is_active here - it causes the app to crash
            # before the emergency exit can complete. The retry loop needs the app to be active.
            # self.tws_app.is_active = False  # REMOVED - this was causing crashes

            # Ensure all open orders are cancelled
            for order_id in self.position.open_orders:
                order = self.position.pool[order_id]
                if order.order_type != OrderType.EMERGENCY_EXIT:
                    self.cancel_order(order)

            logger.debug(
                f"({self.assignment.ticker}) current shares remaining: {self.position.true_share_count}"
            )

            logger.warning(
                f"[{self.assignment.ticker}] Emergency exit protocol called: final={final}, "
                f"is_emergency_exit={self.is_emergency_exit}, true_share_count={self.position.true_share_count}"
            )

            # If position is already closed, just set the flag and return
            if self.position.true_share_count == 0:
                logger.info(f"[{self.assignment.ticker}] Position already closed, setting emergency exit flag to False")
                self.is_emergency_exit = False
                # Only stop the app if position is fully closed
                if hasattr(self.tws_app, 'stop'):
                    self.tws_app.stop()
                return

            # Set emergency exit flag
            self.is_emergency_exit = True

            # Try to start the retry loop if not already running
            retry_loop_started = False
            if hasattr(self.tws_app, '_emergency_retry_active') and self.tws_app._emergency_retry_active:
                logger.info(f"[{self.assignment.ticker}] Emergency exit retry loop already running")
                retry_loop_started = True
            else:
                logger.info(f"[{self.assignment.ticker}] Starting emergency exit retry loop...")
                try:
                    retry_loop_started = self.tws_app.start_emergency_exit_retry_loop()
                    if retry_loop_started:
                        # Don't place initial order here - let the retry loop handle all orders
                        logger.info(f"[{self.assignment.ticker}] Emergency exit retry loop started successfully")
                    else:
                        logger.error(f"[{self.assignment.ticker}] Failed to start emergency exit retry loop")
                except Exception as e:
                    logger.error(f"[{self.assignment.ticker}] Exception starting emergency exit retry loop: {e}")
                    retry_loop_started = False

            # If retry loop couldn't start, place a fallback emergency exit order
            if not retry_loop_started:
                logger.warning(f"[{self.assignment.ticker}] Retry loop failed to start, placing fallback emergency exit order")
                self._place_emergency_exit_order()

        finally:
            self._emergency_exit_lock = False

    def _place_emergency_exit_order(self) -> None:
        """
        Fallback method to place a single emergency exit order (original behavior).
        """
        exit_size = abs(self.position.true_share_count)
        
        # Determine emergency exit price for fast execution:
        # For selling positions (long), use bid price for immediate execution
        # For buying to cover (short), use ask price for immediate execution
        emergency_action = OrderAction.SELL if self.position.true_share_count > 0 else OrderAction.BUY
        emergency_price = self.market_data.bid if emergency_action == OrderAction.SELL else self.market_data.ask
        
        if emergency_exit_order := self.position.emergency_exit_order:
            if emergency_exit_order.requires_update(exit_size, emergency_price):
                emergency_exit_order.size = exit_size
                emergency_exit_order.limit_price = emergency_price
                try:
                    self.modify_order(emergency_exit_order)
                except CannotModifyFilledOrderError as e:
                    logger.warning(f"Cannot modify EMERGENCY EXIT order: {str(e)}")
                    self.cancel_order(emergency_exit_order)
        else:
            # Determine action based on actual TWS position count
            if self.position.true_share_count == 0: # Should not happen if check above passed, but safety first
                 logger.warning(f"[{self.assignment.ticker}] Attempting emergency exit but true_share_count is already 0. Skipping order placement.")
                 return 
            self.place_order(
                order_type=OrderType.EMERGENCY_EXIT,
                order_action=emergency_action, # Use calculated action
                size=exit_size,
                price=emergency_price,
            )
