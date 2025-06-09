"""
The Monger Trading Algorithm
---------------------------
"""

from concurrent.futures import ThreadPoolExecutor
from typing import List

import anyio
from loguru import logger

from app import TradeMonger
from portfolio_app import PortfolioManager
from schema.assignment import TraderAssignment
from predictions.prediction_signals import ClickhouseSignalProvider


class MongerManager:
    """
    Manages and oversees multiple TradeMonger instances.

    :param List[TraderAssignment] assignments: List of trader assignments to manage.
    :param int max_workers: Maximum number of worker threads. Defaults to 10.
    :param List[TradeMonger] mongers: List of TradeMonger instances.
    :param ThreadPoolExecutor executor: Executor for running mongers.
    :param asyncio.AbstractEventLoop loop: The asyncio event loop.
    :param bool running: Flag indicating if the manager is running.
    """

    def __init__(
        self,
        assignments: List[TraderAssignment],
        account: str,
        max_pnl: float = 1000,
        max_workers: int = 10,
        host: str = "127.0.0.1",
        port: int = 7497,
    ):
        """
        Initialize the MongerManager with trader assignments and a thread pool executor.

        :param List[TraderAssignment] assignments: List of trader assignments to manage.
        :param int max_workers: Maximum number of worker threads.
        """
        self.assignments = assignments
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.mongers: List[TradeMonger] = []
        self.running: bool = False
        self._stopped_event = anyio.Event()
        self.host = host
        self.port = port
        self.account = account

        # Prepare ticker_configs for the signal provider
        ticker_configs = [
            {"ticker": a.ticker, "inverted": a.inverted}
            for a in assignments
        ]
        
        # Initialize the signal provider
        self.signal_provider = ClickhouseSignalProvider(ticker_configs=ticker_configs)
        
        # Initialize PortfolioManager FIRST, passing the empty mongers list
        self.portfolio_manager = PortfolioManager(
            account, self.mongers, max_pnl=max_pnl, cancel_func=self.stop
        )
        # Now, initialize TradeMonger instances, passing the portfolio_manager reference
        for assignment in self.assignments:
            try:
                # Create monger, passing self.portfolio_manager
                monger = TradeMonger(
                    assignment=assignment, 
                    account_id=self.account, 
                    signal_provider=self.signal_provider,
                    portfolio_manager=self.portfolio_manager # Pass the manager instance
                )
                self.mongers.append(monger) # Add the created monger to the list
            except Exception as e:
                import traceback
                traceback.print_exc()
                logger.error(f"Failed to initialize TradeMonger for {assignment.ticker}: {e}")
        
        # Ensure the PortfolioManager has the final list of mongers (if needed after init)
        # self.portfolio_manager.mongers = self.mongers # This line might be needed if PortfolioManager uses the list *after* its __init__

    async def start(self, auto_activate: bool = False) -> None:
        """
        Starts the MongerManager by initializing and running all TradeMonger instances.

        Sets up the asyncio event loop, initializes TradeMonger instances for each
        trader assignment, and begins their execution in separate threads.
        """
        if self.running:
            return

        logger.info("Starting The Trade Monger Manager")
        self.running = True

        # Start the signal provider polling
        try:
            self.signal_provider.start_polling()
        except Exception as e:
            logger.error(f"Failed to start ClickHouse Signal Provider: {e}")
            # Decide if we should proceed without signals or stop
            # For now, let's log the error and continue, but mongers will fail
            # unless TradeMonger is updated to handle a missing provider.
            # Consider adding `return` here if signals are essential.

        async with anyio.create_task_group() as tg:
            # Iterate through the already initialized self.mongers
            for monger in self.mongers:
                if auto_activate:
                    monger.set_active(True)
                # Find the corresponding assignment to get the client_id
                # This assumes assignment order matches monger order, which should be true based on init
                assignment = next((a for a in self.assignments if a.ticker == monger.ticker), None)
                if assignment:
                    tg.start_soon(self.run_monger, monger, assignment.client_id)
                else:
                    logger.error(f"Could not find assignment for monger {monger.ticker} during start")
            
            # Start the portfolio manager loop
            try:
                tg.start_soon(self.run_portfolio_manager)
            except Exception:
                import traceback

                traceback.print_exc()
                logger.error("Error creating portfolio manager.")
        
        # Wait here until stop() is called
        await self._stopped_event.wait()
        logger.info("Manager start method completed after stop signal.")

    async def run_monger(self, monger: TradeMonger, client_id: int) -> None:
        try:
            monger.connect(self.host, self.port, clientId=client_id)
            await anyio.to_thread.run_sync(monger.run)
        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.error(f"Error in monger {monger.ticker}: {e}")

    async def run_portfolio_manager(self) -> None:
        try:
            self.portfolio_manager.connect(self.host, self.port, clientId=0)
            await anyio.to_thread.run_sync(self.portfolio_manager.run)
        except Exception as e:
            import traceback

            traceback.print_exc()
            logger.error(f"Error in portfolio manager: {e}")

    async def stop(self) -> None:
        if not self.running:
            return

        self.running = False
        logger.info("Stopping The Trade Monger Manager")
        self._stopped_event.set()

        # --- Trigger Emergency Exit First ---
        logger.info("Initiating emergency exit for all active mongers...")
        for monger in self.mongers:
            # We call this directly, not in the task group, as it queues the action
            try:
                 monger.trigger_emergency_exit()
            except Exception as e:
                 logger.error(f"Error triggering emergency exit for {monger.ticker}: {e}")
        # Allow a brief moment for exit orders to potentially be placed before full stop
        await anyio.sleep(1.0) 
        # -------------------------------------

        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, self.portfolio_manager.stop)
            for monger in self.mongers:
                tg.start_soon(anyio.to_thread.run_sync, monger.stop)
            # Add stopping the signal provider
            tg.start_soon(anyio.to_thread.run_sync, self.signal_provider.stop_polling)

        logger.debug("Shutting down executor")
        await anyio.to_thread.run_sync(self.executor.shutdown)
        logger.info("All mongers stopped successfully")
