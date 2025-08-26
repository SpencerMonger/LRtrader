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
from schema.assignment import TraderAssignment, AssignmentFactory, OrderTimeouts
from predictions.composite_signal_provider import CompositeSignalProvider


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
        config_path: str = None,
        max_pnl: float = 1000,
        max_workers: int = 10,
        host: str = "127.0.0.1",
        port: int = 7497,
    ):
        """
        Initialize the MongerManager with trader assignments and a thread pool executor.

        :param List[TraderAssignment] assignments: List of trader assignments to manage.
        :param str config_path: Path to configuration file for signal settings.
        :param int max_workers: Maximum number of worker threads.
        """
        self.assignments = assignments
        self.config_path = config_path
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
        
        # Create signal configuration from config file
        signal_config_dict = {}
        staggered_order_delay = 5.0  # Default value
        entry_order_timeout = 5  # Default value
        exit_order_timeout = 10  # Default value
        if self.config_path:
            try:
                signal_config = AssignmentFactory.create_signal_config(self.config_path)
                signal_config_dict = {
                    'enable_predictions': signal_config.enable_predictions,
                    'enable_news_alerts': signal_config.enable_news_alerts,
                    'news_alert_lookback_minutes': signal_config.news_alert_lookback_minutes,
                    'enable_dynamic_discovery': signal_config.enable_dynamic_discovery
                }
                # Extract staggered_order_delay from signal_config
                staggered_order_delay = getattr(signal_config, 'staggered_order_delay', 5.0)
                
                # Load order timeout configuration
                order_timeouts_config = AssignmentFactory.create_order_timeouts_config(self.config_path)
                entry_order_timeout = order_timeouts_config.entry_order_timeout
                exit_order_timeout = order_timeouts_config.exit_order_timeout
                
                logger.info(f"Loaded signal config from {self.config_path} with staggered_order_delay: {staggered_order_delay}s")
                logger.info(f"Loaded order timeouts: entry={entry_order_timeout}s, exit={exit_order_timeout}s")
            except Exception as e:
                logger.error(f"Failed to load signal config from {self.config_path}: {e}")
                # Use default config for backward compatibility
                signal_config_dict = {
                    'enable_predictions': True,
                    'enable_news_alerts': False,
                    'news_alert_lookback_minutes': 3,
                    'enable_dynamic_discovery': False
                }
        else:
            # Default configuration for backward compatibility
            signal_config_dict = {
                'enable_predictions': True,
                'enable_news_alerts': False,
                'news_alert_lookback_minutes': 3,
                'enable_dynamic_discovery': False
            }
        
        # Store timeout configurations as instance variables for use in _handle_new_ticker
        self.staggered_order_delay = staggered_order_delay
        self.entry_order_timeout = entry_order_timeout
        self.exit_order_timeout = exit_order_timeout
        
        # Initialize the composite signal provider
        try:
            self.signal_provider = CompositeSignalProvider(
                ticker_configs=ticker_configs,
                signal_config=signal_config_dict
            )
            
            # Set up callback for dynamic ticker discovery
            self.signal_provider.set_new_ticker_callback(self._handle_new_ticker)
            
            logger.info("Initialized CompositeSignalProvider")
        except Exception as e:
            logger.error(f"Failed to initialize CompositeSignalProvider: {e}")
            # Fall back to a basic provider if needed
            raise
        
        # Initialize PortfolioManager FIRST, passing the empty mongers list
        self.portfolio_manager = PortfolioManager(
            account, self.mongers, max_pnl=max_pnl, cancel_func=self.stop
        )
        
        # Now, initialize TradeMonger instances, passing the portfolio_manager reference
        for assignment in self.assignments:
            try:
                # Create monger, passing self.portfolio_manager and timeout configs
                monger = TradeMonger(
                    assignment=assignment, 
                    account_id=self.account, 
                    signal_provider=self.signal_provider,
                    portfolio_manager=self.portfolio_manager, # Pass the manager instance
                    staggered_order_delay=self.staggered_order_delay,
                    entry_order_timeout=self.entry_order_timeout,
                    exit_order_timeout=self.exit_order_timeout
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
            
            # Log the status of all signal providers
            provider_status = self.signal_provider.get_provider_status()
            for provider_name, status in provider_status.items():
                if status.get('initialized', False):
                    logger.info(f"Started {provider_name} signal provider")
                else:
                    logger.warning(f"Failed to start {provider_name} signal provider: {status.get('error', 'Unknown error')}")
                    
        except Exception as e:
            logger.error(f"Failed to start signal providers: {e}")
            # Decide if we should proceed without signals or stop
            # For now, let's log the error and continue, but mongers will fail
            # unless TradeMonger is updated to handle a missing provider.
            # Consider adding `return` here if signals are essential.

        async with anyio.create_task_group() as tg:
            # Start existing mongers
            for monger in self.mongers[:]:  # Use slice copy to avoid modification during iteration
                if auto_activate:
                    monger.set_active(True)
                # Find the corresponding assignment to get the client_id
                assignment = next((a for a in self.assignments if a.ticker == monger.ticker), None)
                if assignment:
                    tg.start_soon(self.run_monger, monger, assignment.client_id)
                else:
                    # This could be a dynamic trader - use its assignment's client_id
                    tg.start_soon(self.run_monger, monger, monger.assignment.client_id)
            
            # Start the portfolio manager loop
            try:
                tg.start_soon(self.run_portfolio_manager)
            except Exception:
                import traceback

                traceback.print_exc()
                logger.error("Error creating portfolio manager.")
                
            # Start dynamic trader management loop
            tg.start_soon(self._dynamic_trader_manager)
        
        # Wait here until stop() is called
        await self._stopped_event.wait()
        logger.info("Manager start method completed after stop signal.")

    async def _dynamic_trader_manager(self) -> None:
        """
        Continuously monitors for new dynamic traders and starts them.
        """
        logger.info("Starting dynamic trader manager")
        started_mongers = set()
        
        while self.running:
            try:
                # Check for new mongers that need to be started
                for monger in self.mongers:
                    # Check if this is a dynamic trader that hasn't been started yet
                    if (monger.assignment.dynamic_client_id is not None and 
                        monger.ticker not in started_mongers and 
                        monger.is_active):
                        
                        # Start this dynamic trader
                        client_id = monger.assignment.client_id
                        logger.info(f"🚀 Starting dynamic trader for {monger.ticker} with client ID {client_id}")
                        
                        # Connect and run the monger
                        try:
                            monger.connect(self.host, self.port, clientId=client_id)
                            # Start in background thread
                            import threading
                            trader_thread = threading.Thread(
                                target=monger.run,
                                daemon=True,
                                name=f"DynamicTrader-{monger.ticker}"
                            )
                            trader_thread.start()
                            started_mongers.add(monger.ticker)
                            logger.info(f"✅ Dynamic trader for {monger.ticker} started successfully")
                            
                        except Exception as e:
                            logger.error(f"Failed to start dynamic trader for {monger.ticker}: {e}")
                
                # Sleep for a short time before checking again
                await anyio.sleep(1.0)
                
            except Exception as e:
                logger.error(f"Error in dynamic trader manager: {e}")
                await anyio.sleep(5.0)  # Wait longer on error
        
        logger.info("Dynamic trader manager stopped")

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

        # CRITICAL: Log who called stop() with full stack trace
        import traceback
        import inspect
        from datetime import datetime
        
        # Get the complete call stack
        stack = traceback.format_stack()
        caller_info = inspect.stack()[1]
        
        # Log to multiple places to prevent silent failures
        logger.critical("="*80)
        logger.critical("EMERGENCY SHUTDOWN INITIATED")
        logger.critical(f"Called from: {caller_info.filename}:{caller_info.lineno} in {caller_info.function}")
        logger.critical("Full call stack:")
        for frame in stack:
            logger.critical(frame.strip())
        logger.critical("="*80)
        
        # Also write directly to file as backup
        try:
            with open("logs/emergency_shutdown.log", "a") as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"EMERGENCY SHUTDOWN at {datetime.now()}\n")
                f.write(f"Called from: {caller_info.filename}:{caller_info.lineno} in {caller_info.function}\n")
                f.write("Full call stack:\n")
                for frame in stack:
                    f.write(frame)
                f.write(f"{'='*80}\n")
        except Exception as e:
            print(f"Failed to write emergency shutdown log: {e}")
        
        # Also print to stdout as last resort
        print(f"EMERGENCY SHUTDOWN INITIATED at {datetime.now()}")
        print(f"Called from: {caller_info.filename}:{caller_info.lineno}")

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

    def get_signal_summary(self) -> dict:
        """Get a summary of all signal providers for monitoring."""
        try:
            return self.signal_provider.get_signal_summary()
        except Exception as e:
            logger.error(f"Error getting signal summary: {e}")
            return {}

    def get_provider_status(self) -> dict:
        """Get the status of all signal providers."""
        try:
            return self.signal_provider.get_provider_status()
        except Exception as e:
            logger.error(f"Error getting provider status: {e}")
            return {}

    def _handle_new_ticker(self, ticker: str, price: float = None) -> None:
        """
        Handle discovery of a new ticker from news alerts by creating a dynamic trader.
        
        :param ticker: The ticker symbol that was discovered
        :param price: The current price of the ticker (used for tier-based position sizing)
        """
        try:
            # Check if we already have a trader for this ticker
            existing_trader = next((m for m in self.mongers if m.ticker == ticker), None)
            if existing_trader:
                logger.debug(f"Trader for {ticker} already exists, skipping creation")
                return
            
            # Check if this is a global configuration that supports dynamic creation
            if not self.config_path:
                logger.warning(f"Cannot create dynamic trader for {ticker} - no config path available")
                return
                
            try:
                config = AssignmentFactory.load_config(self.config_path)
                if "global_defaults" not in config:
                    logger.warning(f"Cannot create dynamic trader for {ticker} - no global_defaults in config")
                    return
                
                # Generate dynamic client ID for the new ticker
                client_id = self._generate_dynamic_client_id(ticker)
                
                # Create dynamic assignment using global defaults and price-based tier sizing
                dynamic_assignment = AssignmentFactory.create_dynamic_assignment(ticker, self.config_path, client_id, price)
                
                # Create new TradeMonger instance
                monger = TradeMonger(
                    assignment=dynamic_assignment,
                    account_id=self.account,
                    signal_provider=self.signal_provider,
                    portfolio_manager=self.portfolio_manager,
                    staggered_order_delay=self.staggered_order_delay,
                    entry_order_timeout=self.entry_order_timeout,
                    exit_order_timeout=self.exit_order_timeout
                )
                
                # Add to our mongers list
                self.mongers.append(monger)
                
                # Start the new monger in a separate task if manager is running
                if self.running:
                    import anyio
                    # We need to start this in the event loop
                    price_info = f" at ${price:.2f}" if price is not None else " (price unavailable)"
                    logger.info(f"🚀 Starting dynamic trader for {ticker}{price_info} with client ID {client_id}")
                    # Note: We'll need to handle this differently since we're not in async context
                    # For now, mark it as active and it will start on next cycle
                    monger.set_active(True)
                    
                    # Store client_id for connection (assignment already has it)
                    # No need to store separately since it's in the assignment
                
                price_info = f" at ${price:.2f}" if price is not None else " (price unavailable)"
                logger.info(f"✅ Created dynamic trader for {ticker}{price_info} using tier-based position sizing")
                
            except Exception as e:
                logger.error(f"Failed to create dynamic assignment for {ticker}: {e}")
                
        except Exception as e:
            logger.error(f"Error in _handle_new_ticker for {ticker}: {e}")
            
    def _generate_dynamic_client_id(self, ticker: str) -> int:
        """
        Generate a unique client ID for a dynamic ticker.
        
        :param ticker: The ticker symbol
        :return: Unique client ID
        """
        # Use hash-based generation starting from 10M range to avoid conflicts
        base_hash = abs(hash(ticker)) % 1000000
        client_id = 10000000 + base_hash
        
        # Check for conflicts with existing traders
        existing_client_ids = set()
        for assignment in self.assignments:
            existing_client_ids.add(assignment.client_id)
        for monger in self.mongers:
            existing_client_ids.add(monger.assignment.client_id)
        
        # Handle collisions by incrementing
        while client_id in existing_client_ids:
            client_id += 1
            if client_id > 11000000:  # Safety limit
                raise ValueError(f"Cannot generate unique client ID for {ticker}")
        
        logger.info(f"Generated dynamic client ID {client_id} for {ticker}")
        return client_id
