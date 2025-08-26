"""
The brokerage module handles communication with the Interactive Brokers TWS API.
"""

import threading

from anyio.from_thread import start_blocking_portal
from ibapi.common import OrderId
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.order_state import OrderState
from loguru import logger

from app import TradeMonger
from clients import MongerClient, PortfolioWrapper
from schema.enums import OrderStatus
from schema.order import OrderType


class PortfolioManager(PortfolioWrapper, MongerClient):
    """
    PortfolioManager handles the communication with the IBKR API for account-wide operations.
    """

    def __init__(
        self, account: str, mongers: list[TradeMonger], max_pnl: float, cancel_func: callable
    ):
        PortfolioWrapper.__init__(self)
        MongerClient.__init__(self, wrapper=self)

        self.account = account
        self.mongers = mongers
        self.max_pnl = max_pnl
        self.cancel_func = cancel_func

        self.running = False
        self.stop_event = threading.Event()
        self.task_group = None
        self.event_loop = None

        self.order_to_monger_mapping = dict()

    def nextValidId(self, orderId: OrderId) -> None:
        """
        Callback when the next valid order ID is received.
        """
        super().nextValidId(orderId)
        logger.info(f"Next Valid Order ID: {orderId}")

        # DEBUG: Log the exact PnL request details
        logger.critical(f"🔍 DEBUG: About to make PnL request - Account: '{self.account}', ReqId: 1001, Max PnL: {self.max_pnl}")
        
        try:
            # Initial request for account updates
            self.reqPnL(1001, self.account, "")
            logger.critical("✅ DEBUG: reqPnL() call completed successfully - waiting for pnl() callbacks...")
        except Exception as e:
            logger.critical(f"❌ DEBUG: reqPnL() call FAILED with exception: {e}")
            import traceback
            logger.critical(f"Full exception traceback: {traceback.format_exc()}")

        # Request the orders
        self.reqAutoOpenOrders(True)
        logger.info("🔍 DEBUG: reqAutoOpenOrders() call completed")

    def run(self) -> None:
        self.running = True

        logger.success(f"Starting Portfolio manager for account {self.account}")

        # Run the IB API
        super(MongerClient, self).run()

    def stop(self) -> None:
        self.running = False

        logger.success("Stopping Portfolio manager")
        self.disconnect()

    def check_pnl_threshold(self, pnl: float):
        """
        Check if total PnL exceeds the maximum loss threshold and trigger emergency exit if necessary.
        
        :param pnl: Total PnL (realized + unrealized) for the account
        """
        # DEBUG: Always log threshold checks so we can see the system working
        logger.critical(f"🛡️ DEBUG: PnL Threshold Check - Current Total PnL: ${pnl:.2f}, Max Loss Threshold: ${self.max_pnl:.2f}")
        
        if pnl < self.max_pnl:
            logger.critical(
                f"🚨🚨🚨 PORTFOLIO RISK LIMIT BREACHED! 🚨🚨🚨 "
                f"Total PnL (${pnl:.2f}) has exceeded maximum loss threshold (${self.max_pnl:.2f}). "
                f"Triggering emergency shutdown of all positions."
            )
            try:
                with start_blocking_portal(backend="asyncio") as portal:
                    portal.call(self.cancel_func)
                logger.critical("✅ DEBUG: Emergency shutdown triggered successfully")
            except Exception as e:
                logger.critical(f"❌ DEBUG: Emergency shutdown FAILED: {e}")
        else:
            # Calculate how close we are to the limit
            remaining_capacity = pnl - self.max_pnl
            logger.info(f"✅ DEBUG: Risk check PASSED - Remaining loss capacity: ${remaining_capacity:.2f}")
            
            # Only log when we're getting close to the threshold (within 80%)
            threshold_ratio = pnl / self.max_pnl if self.max_pnl != 0 else 0
            if threshold_ratio > 0.8:
                logger.warning(
                    f"⚠️ WARNING: Approaching risk limit! Total PnL = ${pnl:.2f}, "
                    f"Remaining loss capacity = ${remaining_capacity:.2f} (at {threshold_ratio*100:.1f}% of limit)"
                )

    def openOrder(self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState):
        # Find the monger for the ticker
        monger = None
        for _monger in self.mongers:
            if _monger.assignment.ticker == contract.symbol:
                monger = _monger
                break

        self.order_to_monger_mapping[orderId] = monger

    def orderStatus(
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ) -> None:
        # Start by getting the monger for the order
        monger = self.order_to_monger_mapping.get(orderId, None)

        if monger is None:
            logger.warning(f"No monger found for order ID: {orderId} -- ignoring order.")

        # Otherwise, we need to plan our call to monger.position.remove_from_position
        if status == OrderStatus.FILLED:
            num_shares = float(filled)
            price = float(avgFillPrice)

            logger.warning(
                f"Manually entering removing {num_shares} @ ${price} "
                f"from {monger.assignment.ticker}"
            )
            monger.order_executor.position.remove_from_position(
                orderId, num_shares, price, is_manual=True
            )

            # Cancel the orders that must be cancelled
            for order in monger.order_executor.position.orders_to_cancel:
                monger.order_executor.cancel_order(order)

    def sweep_stale_bracket_orders(self):
        """
        Iterates through all managed tickers and cancels any lingering
        TAKE_PROFIT or STOP_LOSS orders if the TWS reported position is zero.
        Useful for cleaning up orders that might have been orphaned due to 
        past inconsistencies.
        """
        logger.info("Starting global sweep for stale bracket orders...")
        cancelled_count = 0
        for monger in self.mongers:
            ticker = "UNKNOWN"
            try:
                # Ensure necessary components exist before proceeding
                if not hasattr(monger, 'order_executor') or \
                   not hasattr(monger.order_executor, 'position') or \
                   not hasattr(monger, 'assignment'):
                    logger.warning(f"Skipping sweep for a monger missing required attributes.")
                    continue
                    
                position = monger.order_executor.position
                executor = monger.order_executor
                ticker = monger.assignment.ticker

                # Check if TWS reports zero position for this ticker
                if position.true_share_count == 0:
                    orders_to_check = []
                    # Use properties which fetch from the pool
                    orders_to_check.extend(position.stop_loss_orders)
                    orders_to_check.extend(position.take_profit_orders)
                    
                    if not orders_to_check:
                        continue # No brackets in the pool for this zero-pos ticker
                        
                    logger.warning(f"[{ticker}] TWS pos is 0, sweeping {len(orders_to_check)} potential stale bracket orders.")
                    for order in orders_to_check:
                         # Double-check the order is actually still in the live pool index before cancelling
                         # (The property list might be slightly stale)
                         if order.order_id in position.pool.index:
                             # Verify type again just in case pool contains unexpected items
                             if order.order_type in [OrderType.STOP_LOSS, OrderType.TAKE_PROFIT]:
                                 logger.debug(f"[{ticker}] Cancelling stale {order.order_type.value} order ID: {order.order_id}")
                                 executor.cancel_order(order)
                                 cancelled_count += 1
                             else:
                                  logger.warning(f"[{ticker}] Found non-bracket order type ({order.order_type.value}) during sweep? ID: {order.order_id}. Skipping.")
                         # else: Order already removed/cancelled, no action needed.

            except AttributeError as e:
                logger.error(f"AttributeError accessing components for ticker {ticker} during sweep: {e}. Skipping.")
            except Exception as e:
                # Catch other potential errors during the sweep for a single monger
                logger.error(f"Unexpected error during sweep for ticker {ticker}: {e}")

        logger.info(f"Global stale bracket order sweep finished. Attempted to cancel {cancelled_count} orders.")
