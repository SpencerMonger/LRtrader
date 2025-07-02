"""
The brokerage module handles communication with the Interactive Brokers TWS API.

    For more information on implementing the TWS API, please refer to their documentation here:
https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/#api-introduction

For a step-by-step guide on installing the `ibapi` package, please review the detailed guide in
the Monger Notion wiki:
https://www.notion.so/IB-API-Source-Code-Setup-ba3636ee75c34e128250354612093405?pvs=4
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import threading
import time
import traceback
from typing import TYPE_CHECKING

import anyio
from ibapi.common import OrderId
from ibapi.contract import Contract
from loguru import logger

from clients import MongerClient, MongerWrapper
from clients.ibkr_wrapper import BID_ASK_REQ_ID, POSITION_PNL_REQ_ID, TRAILING_15_MIN_REQ_ID
from schema import TraderAssignment
from schema.enums import OrderType

# Use correct imports from schema
from src.schema.prediction import PriceDirection, Prediction # Import enum and Prediction model
from predictions.composite_signal_provider import CompositeSignalProvider # Import CompositeSignalProvider

if TYPE_CHECKING:
    from portfolio_app import PortfolioManager # Ensure this import is present


class TradeMonger(MongerWrapper, MongerClient):
    """
    TradeMonger is the main class that handles the communication with the IBKR API.

    :param str ticker: The ticker symbol to trade.
    :param Contract contract: The contract object for the ticker symbol.
    :param dict book_data: The shared data structure for storing the order book data.
    :param threading.Lock book_lock: The lock for the shared `book_data` structure.
    :param threading.Thread inference_thread: The thread for running the inference loop.
    :param bool running: A flag to indicate if the TradeMonger is running.
    :param Inference inference: The inference component for making predictions.
    :param threading.Event stop_event: The event to signal the inference loop to stop.
    :param asyncio.AbstractEventLoop loop: The asyncio event loop for running coroutines.

    """

    def __init__(self, assignment: TraderAssignment, account_id: str, signal_provider: CompositeSignalProvider, portfolio_manager: "PortfolioManager", staggered_order_delay: float = 5.0):
        # Pass portfolio_manager and staggered_order_delay to MongerWrapper superclass
        MongerWrapper.__init__(self, assignment=assignment, portfolio_manager=portfolio_manager, staggered_order_delay=staggered_order_delay)
        MongerClient.__init__(self, wrapper=self)

        self.account_id = account_id
        self.signal_provider = signal_provider

        # Establish data structures and variables
        self.contract = Contract()
        self.contract.symbol = self.assignment.ticker
        self.contract.secType = "STK"
        self.contract.exchange = "SMART"
        self.contract.currency = "USD"
        self.contract.primaryExchange = "NASDAQ"

        # Configure threading management
        self.position_thread = None
        self.running = False
        self.stop_event = threading.Event()

        # Make sure that the order executor can manage the order IDs
        self.nextValidOrderId = None

        self.is_active = False  # Add this line to initialize the active flag

        self.position_pnl_request = False

        self.task_group = None
        self.event_loop = None
        self.stop_event = threading.Event()

    @property
    def ticker(self) -> str:
        """
        The ticker symbol for the TradeMonger.

        :return str: The ticker symbol.
        """
        return self.assignment.ticker

    def nextValidId(self, orderId: OrderId) -> None:
        """
        Callback when the next valid order ID is received.
        Starts market data subscription and inference thread.

        :param OrderId orderId: The next valid order ID
        """
        super().nextValidId(orderId)
        logger.info(f"Next Valid Order ID: {orderId}")

        self.nextValidOrderId = orderId

        self.reqMarketDataType(1)
        self.reqMktData(BID_ASK_REQ_ID, self.contract, "", False, False, [])

        # Request historical data
        self.reqHistoricalData(
            TRAILING_15_MIN_REQ_ID,  # reqId
            self.contract,
            "",  # endDateTime (empty string means "now")
            "900 S",  # durationStr (15 minutes)
            "1 min",  # barSizeSetting
            "TRADES",  # whatToShow
            0,  # useRTH
            2,  # formatDate
            False,  # keepUpToDate
            [],  # chartOptions
        )

        self.reqPositions()

    def nextOrderId(self):
        """
        Get the next valid order ID.
        """
        oid = self.nextValidOrderId
        if oid is None:
            self.nextValidOrderId = self.nextValidId()
        self.nextValidOrderId += 1
        return oid

    # NOTE: PERSISTENT THREAD
    async def historical_data_loop(self):
        logger.debug("starting data loop")
        try:
            while not self.stop_event.is_set():
                now = datetime.now()
                next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                try:
                    await anyio.sleep((next_minute - now).total_seconds())
                except anyio.get_cancelled_exc_class():
                    break

                if self.stop_event.is_set():
                    break

                self.reqHistoricalData(
                    TRAILING_15_MIN_REQ_ID,
                    self.contract,
                    "",
                    "900 S",
                    "1 min",
                    "TRADES",
                    0,
                    2,
                    False,
                    [],
                )
        except Exception as e:
            traceback.print_exc()
            logger.error(f"{self.ticker} -- UNHANDLED EXCEPTION IN HISTORICAL DATA LOOP: {e}")

            # Restart the historical data loop
            logger.warning(f"{self.ticker} -- Restarting historical data loop.")
            # Note: Restarting the whole loop structure might be complex.
            # Consider specific error handling or allowing manager to restart monger.
            # For now, just logging the intent to restart.
            # return self.inference_loop() # This was likely incorrect logic anyway

    # Replaced inference_loop with signal_check_loop
    async def signal_check_loop(self):
        logger.debug(f"{self.ticker} -- Starting signal check loop")
        last_signal_time = None
        try:
            while not self.stop_event.is_set():
                # Log loop execution for this ticker
                logger.trace(f"[{self.ticker}] Running signal check iteration.")
                # Check for signals more frequently, e.g., every 1 second
                try:
                    await anyio.sleep(1)
                except anyio.get_cancelled_exc_class():
                    break

                if self.stop_event.is_set():
                    break

                # Log before getting signal
                logger.trace(f"[{self.ticker}] Checking signal provider...")
                signal_data = self.signal_provider.get_latest_signal(self.ticker)
                # Use DEBUG level for signal data visibility
                logger.debug(f"[{self.ticker}] Signal provider returned: {signal_data}")

                if signal_data:
                    # Get the current flag and timestamp
                    current_flag = signal_data['flag']
                    current_signal_time = signal_data.get('timestamp')
                    signal_source = signal_data.get('source', 'unknown')

                    # Log values before comparison
                    logger.debug(f"[{self.ticker}] Checking signal: Current Time={current_signal_time}, Last Processed Time={last_signal_time}, Source={signal_source}")
                    # Only process if the timestamp is newer than the last processed one
                    if last_signal_time is None or current_signal_time > last_signal_time:
                        # Log that check passed
                        logger.debug(f"[{self.ticker}] Signal timestamp is new. Proceeding...")
                        logger.info(
                            f"SIGNAL -- {self.ticker}: "
                            f"{current_flag} (From {signal_source} @ {current_signal_time})"
                        )
                        # Create an actual Prediction object
                        prediction = Prediction(
                            flag=current_flag,
                            confidence=1.0 # Hardcoded confidence
                            # timestamp is not part of Prediction model
                        )
                        # Log before calling handler
                        logger.debug(f"[{self.ticker}] New signal time {current_signal_time}. Is Active: {self.is_active}. Calling handler...")
                        if self.is_active:
                            # Call the existing handler with the Prediction object
                            self.order_executor.handle_prediction(prediction)
                        # Update last processed time
                        last_signal_time = current_signal_time
                    else:
                        # Log that the signal is unchanged and being ignored by this loop
                        logger.debug(f"[{self.ticker}] Signal timestamp {current_signal_time} is not newer than last processed {last_signal_time}. Ignoring.")

        except Exception as e:
            traceback.print_exc()
            logger.error(f"{self.ticker} -- UNHANDLED EXCEPTION IN SIGNAL CHECK LOOP: {e}")
            # Consider more robust error handling/restart strategy here

    # NOTE: PERSISTENT THREAD
    async def position_monitor_loop(self):
        """
        The main position monitor loop that runs every second.
        """
        logger.debug("starting position loop")
        try:
            while not self.stop_event.is_set():
                try:
                    await anyio.sleep(1)
                except anyio.get_cancelled_exc_class():
                    break

                if self.stop_event.is_set():
                    break

                if self.order_executor.contract.conId and not self.position_pnl_request:
                    self.reqPnLSingle(
                        POSITION_PNL_REQ_ID, self.account_id, "", self.order_executor.contract.conId
                    )
                    self.position_pnl_request = True

                self.order_executor.handle_expired_positions()
                self.order_executor.handle_dangling_shares()
                self.order_executor.handle_pnl_checks()
                # CRITICAL FIX: Check position size every second as safety net
                self.order_executor.handle_max_position_size_check()
        except Exception as e:
            traceback.print_exc()
            logger.error(f"{self.ticker} -- UNHANDLED EXCEPTION IN POSITION MONITOR LOOP: {e}")

            # Restart the position monitor loop
            return self.position_monitor_loop()

    # NOTE: EMERGENCY EXIT RETRY LOOP
    async def emergency_exit_retry_loop(self):
        """
        Aggressive emergency exit retry loop that runs every 10 seconds.
        Cancels existing emergency exit orders and places new ones at current bid/ask prices.
        """
        logger.warning(f"[{self.ticker}] Starting aggressive emergency exit retry loop")
        retry_count = 0
        
        try:
            while self.order_executor.is_emergency_exit:
                retry_count += 1
                logger.warning(f"[{self.ticker}] Emergency exit retry attempt #{retry_count}")
                
                # Check if position is fully closed
                current_position = self.order_executor.position.true_share_count
                if current_position == 0:
                    logger.success(f"[{self.ticker}] Position fully closed, stopping emergency exit retry loop")
                    self.order_executor.is_emergency_exit = False
                    break
                
                # Cancel existing emergency exit order if it exists
                if emergency_exit_order := self.order_executor.position.emergency_exit_order:
                    logger.warning(f"[{self.ticker}] Cancelling existing emergency exit order {emergency_exit_order.order_id}")
                    self.order_executor.cancel_order(emergency_exit_order)
                    # Give a moment for the cancellation to process
                    try:
                        await anyio.sleep(0.5)
                    except anyio.get_cancelled_exc_class():
                        break
                
                # Place new emergency exit order at current bid/ask price
                exit_size = abs(current_position)
                if exit_size > 0:
                    # Determine emergency exit price for fast execution:
                    # For selling positions (long), use bid price for immediate execution  
                    # For buying to cover (short), use ask price for immediate execution
                    emergency_action = self.order_executor.position.exit_action
                    
                    # Handle case where position is closed and exit_action is None
                    if emergency_action is None:
                        logger.warning(f"[{self.ticker}] Position exit_action is None, position may have been closed")
                        break
                    
                    # Get current market data
                    bid_price = self.order_executor.market_data.bid
                    ask_price = self.order_executor.market_data.ask
                    
                    # Use bid for selling, ask for buying
                    emergency_price = bid_price if emergency_action.value == "SELL" else ask_price
                    
                    # Ensure we have valid pricing
                    if emergency_price and emergency_price > 0:
                        logger.warning(f"[{self.ticker}] Placing aggressive emergency exit order: "
                                     f"{emergency_action.value} {exit_size} @ ${emergency_price:.2f}")
                        
                        self.order_executor.place_order(
                            order_type=OrderType.EMERGENCY_EXIT,
                            order_action=emergency_action,
                            size=exit_size,
                            price=emergency_price,
                        )
                    else:
                        logger.error(f"[{self.ticker}] Invalid pricing for emergency exit: bid={bid_price}, ask={ask_price}")
                        # Use a fallback price based on last known price or market price
                        fallback_price = self.order_executor.market_data.last_price or 1.0
                        logger.warning(f"[{self.ticker}] Using fallback price ${fallback_price:.2f} for emergency exit")
                        
                        self.order_executor.place_order(
                            order_type=OrderType.EMERGENCY_EXIT,
                            order_action=emergency_action,
                            size=exit_size,
                            price=fallback_price,
                        )
                else:
                    logger.warning(f"[{self.ticker}] Exit size is 0, position may have been closed")
                    break
                
                # Wait 10 seconds before next retry
                try:
                    await anyio.sleep(10)
                except anyio.get_cancelled_exc_class():
                    break
                    
        except Exception as e:
            traceback.print_exc()
            logger.error(f"{self.ticker} -- UNHANDLED EXCEPTION IN EMERGENCY EXIT RETRY LOOP: {e}")
        finally:
            self._emergency_retry_active = False
        
        logger.warning(f"[{self.ticker}] Emergency exit retry loop finished after {retry_count} attempts")

    async def run_loops(self):
        async with anyio.create_task_group() as tg:
            self.task_group = tg
            tg.start_soon(self.historical_data_loop)
            tg.start_soon(self.signal_check_loop)
            tg.start_soon(self.position_monitor_loop)

    def run(self) -> None:
        """
        Run the monger trader instance.
        """
        # Start up auxiliary threads and
        self.running = True

        logger.debug("running = True")

        def _run_async_loops():
            self.event_loop.run_until_complete(self.run_loops())

        # Create and set the event loop
        self.event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.event_loop)

        # Start the async loops in a separate thread
        threading.Thread(target=_run_async_loops, daemon=True).start()

        # Initialize the order executor
        self.order_executor.initialize(self)

        # Run the IB API
        # NOTE: This line is blocking, so nothing can be placed after it
        super(MongerClient, self).run()

    def stop(self, skip_emerg: bool = False):
        logger.info(f"Stopping the Trade Monger for {self.ticker}")
        self.stop_event.set()

        # Execute emergency exit
        logger.info(f"{self.ticker} -- Executing emergency exit protocol")

        if not skip_emerg:
            self.order_executor.execute_emergency_exit()

            while self.order_executor.position.true_share_count != 0:
                logger.debug(
                    f"Waiting for emergency exit to complete -- current status: "
                    f"{self.order_executor.is_emergency_exit} & size: "
                    f"{self.order_executor.position.size}"
                    f"& true share count: {self.order_executor.position.true_share_count}"
                )
                time.sleep(1)
                self.order_executor.execute_emergency_exit(final=True)

        # Cancel all tasks in the task group
        if self.task_group:
            self.event_loop.call_soon_threadsafe(self.task_group.cancel_scope.cancel)

        self.order_executor.shutdown()
        self.disconnect()
        logger.info(f"The TradeMonger for {self.ticker} stopped successfully.")

    def set_active(self, active: bool):
        """
        Set the active flag for the TradeMonger instance.

        :param bool active: Whether the TradeMonger should be active or not.
        """
        self.is_active = active
        logger.info(f"{self.ticker} -- TradeMonger active status set to: {active}")

    def trigger_emergency_exit(self, final: bool = False):
        """
        Public method to initiate the emergency exit protocol via the order executor.
        """
        if hasattr(self, 'order_executor') and self.order_executor.initialized:
            logger.warning(f"[{self.ticker}] Manager requested emergency exit.")
            self.order_executor.execute_emergency_exit(final=final)
        else:
            logger.warning(f"[{self.ticker}] Could not trigger emergency exit: OrderExecutor not initialized.")

    def start_emergency_exit_retry_loop(self):
        """
        Start the emergency exit retry loop dynamically.
        """
        # Check if retry loop is already active - CRITICAL: Do this check BEFORE adding task
        if hasattr(self, '_emergency_retry_active') and self._emergency_retry_active:
            logger.warning(f"[{self.ticker}] Emergency exit retry loop already active, not starting another")
            return True
        
        # Set the flag IMMEDIATELY to prevent race conditions
        self._emergency_retry_active = True
            
        if hasattr(self, 'task_group') and self.task_group:
            try:
                # Simple approach: just try to add the task
                self.event_loop.call_soon_threadsafe(
                    lambda: self.task_group.start_soon(self.emergency_exit_retry_loop)
                )
                logger.warning(f"[{self.ticker}] Emergency exit retry loop startup requested")
                return True
            except Exception as e:
                logger.error(f"[{self.ticker}] Failed to start emergency exit retry loop: {e}")
                # Reset flag on failure
                self._emergency_retry_active = False
                return False
        else:
            logger.warning(f"[{self.ticker}] Task group not available, TradeMonger doesn't support retry loop")
            # Reset flag on failure
            self._emergency_retry_active = False
            return False

    # TODO: Refactor into separate method