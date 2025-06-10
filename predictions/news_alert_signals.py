import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Set, Callable

import clickhouse_connect
import schedule
from dotenv import load_dotenv
from loguru import logger

# Import the correct PriceDirection enum from the schema
import sys
import os
# Add project root to path to allow importing from src
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)
# Import PriceDirection from the correct location
from src.schema.prediction import PriceDirection


class NewsAlertSignalProvider:
    """
    Polls a ClickHouse database for news alerts and generates trading signals 
    when unique tickers appear in the 'News.news_alert' table within a 3-minute lookback period.
    """

    def __init__(self, ticker_configs: list[dict], lookback_minutes: int = 3, enable_dynamic_discovery: bool = False):
        """
        Initializes the provider, loads configuration, and sets up state.

        :param ticker_configs: A list of dictionaries containing ticker configuration
        :param lookback_minutes: Minutes to look back for unique ticker detection (default: 3)
        :param enable_dynamic_discovery: If True, discover ANY ticker from news alerts, not just configured ones
        """
        self._ticker_configs = {config['ticker']: config for config in ticker_configs}
        self._tickers = [config['ticker'] for config in ticker_configs]
        self._lookback_minutes = lookback_minutes
        self._enable_dynamic_discovery = enable_dynamic_discovery
        self._load_config()
        
        # Track signals and seen tickers
        self._latest_signals: Dict[str, dict] = {}  # Stores {'ticker': {'flag': PriceDirection, 'timestamp': datetime}}
        self._seen_tickers: Set[str] = set()  # Track tickers we've already seen to avoid duplicate signals
        self._ticker_last_seen: Dict[str, datetime] = {}  # Track when each ticker was last seen
        
        # Threading management
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._polling_thread = None
        self._client = None
        
        # Callback for new ticker discovery
        self._new_ticker_callback: Optional[Callable[[str, float], None]] = None

        # Table and column configuration for news alerts
        self._timestamp_col = "timestamp"
        self._ticker_col = "ticker"
        self._table_name = "News.news_alert"  # Use the specified table

    def _load_config(self):
        """Loads ClickHouse connection details from .env file."""
        dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        logger.debug(f"Looking for .env file at: {dotenv_path}, Exists: {os.path.exists(dotenv_path)}")
        
        loaded_ok = load_dotenv(dotenv_path=dotenv_path)
        logger.debug(f"load_dotenv result: {loaded_ok}")

        self._db_host = os.getenv("CLICKHOUSE_HOST")
        self._db_port = int(os.getenv("CLICKHOUSE_HTTP_PORT", 8123))
        self._db_user = os.getenv("CLICKHOUSE_USER", "default")
        self._db_password = os.getenv("CLICKHOUSE_PASSWORD", "")
        self._db_database = os.getenv("CLICKHOUSE_DATABASE")
        self._db_secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"

        logger.debug(f"ClickHouse Connection Params: Host={self._db_host}, Port={self._db_port}, Secure={self._db_secure}")

        if not all([self._db_host, self._db_database]):
            raise ValueError("Missing required ClickHouse connection details in predictions/.env (CLICKHOUSE_HOST, CLICKHOUSE_DATABASE)")
        logger.info("ClickHouse configuration loaded for NewsAlertSignalProvider.")

    def _connect(self):
        """Establishes connection to ClickHouse."""
        try:
            if self._client:
                self._client.close()
                
            logger.debug(f"Attempting ClickHouse connection to {self._db_host}:{self._db_port} (secure: {self._db_secure})")
            self._client = clickhouse_connect.get_client(
                host=self._db_host,
                port=self._db_port,
                username=self._db_user,
                password=self._db_password,
                database=self._db_database,
                secure=self._db_secure
            )
            self._client.ping()
            logger.info("Successfully connected to ClickHouse for NewsAlertSignalProvider.")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse for NewsAlertSignalProvider: {e}")
            self._client = None

    def _poll_db(self):
        """Queries the news_alert table for unique tickers in the lookback period."""
        if not self._client:
            logger.warning("No ClickHouse connection, attempting to reconnect.")
            self._connect()
            if not self._client:
                logger.error("Skipping news alert DB poll due to connection failure.")
                return

        logger.debug(f"Polling ClickHouse news alerts for lookback period: {self._lookback_minutes} minutes")
        
        # Calculate the lookback time
        lookback_time = datetime.now() - timedelta(minutes=self._lookback_minutes)
        
        # Query for unique tickers with their latest price in the lookback period
        if self._enable_dynamic_discovery:
            # Dynamic mode: Query ALL tickers from news alerts (no filtering)
            query = f"""
            SELECT DISTINCT {self._ticker_col}, price
            FROM {self._table_name}
            WHERE {self._timestamp_col} >= %(lookback_time)s
            """
            query_params = {'lookback_time': lookback_time}
            logger.debug("Running news alert query in DYNAMIC mode - will discover any ticker")
        else:
            # Static mode: Only query for configured tickers
            query = f"""
            SELECT DISTINCT {self._ticker_col}, price
            FROM {self._table_name}
            WHERE {self._timestamp_col} >= %(lookback_time)s
            AND {self._ticker_col} IN %(configured_tickers)s
            """
            query_params = {
                'lookback_time': lookback_time,
                'configured_tickers': self._tickers
            }
            logger.debug(f"Running news alert query in STATIC mode - limited to {len(self._tickers)} configured tickers")
        
        try:
            result = self._client.query(query, parameters=query_params)
            
            current_time = datetime.now()
            new_signals = {}
            
            # Process each unique ticker found in the news alerts 
            for row in result.result_rows:
                ticker = row[0]
                price = row[1] if len(row) > 1 else None
                
                # Check if this ticker is new or hasn't been seen recently
                last_seen = self._ticker_last_seen.get(ticker)
                
                # Generate signal if:
                # 1. We've never seen this ticker before, OR
                # 2. We haven't seen this ticker in the last lookback period
                should_generate_signal = (
                    last_seen is None or 
                    (current_time - last_seen).total_seconds() > (self._lookback_minutes * 60)
                )
                
                if should_generate_signal:
                    # Default to BULLISH signal for news alerts
                    signal_flag = PriceDirection.BULLISH
                    
                    # Check inversion status for the ticker (use default if not configured)
                    ticker_config = self._ticker_configs.get(ticker, {'inverted': 'regular'})
                    if ticker_config.get('inverted') == 'inverted':
                        signal_flag = PriceDirection.BEARISH
                        logger.debug(f"Inverting news alert signal for {ticker} from BULLISH to BEARISH")
                    
                    # Include price information in the signal
                    signal_data = {
                        'flag': signal_flag, 
                        'timestamp': current_time,
                        'price': price
                    }
                    new_signals[ticker] = signal_data
                    self._ticker_last_seen[ticker] = current_time
                    
                    price_info = f" at ${price:.2f}" if price is not None else " (price unavailable)"
                    logger.info(f"News alert signal generated for {ticker}: {signal_flag.name}{price_info}")
                    
                    # Notify about new ticker discovery (especially for dynamic mode)
                    if self._new_ticker_callback:
                        if ticker not in self._ticker_configs or self._enable_dynamic_discovery:
                            logger.info(f"New ticker discovered in news alerts: {ticker}")
                            # Call callback with price information (may be None)
                            self._new_ticker_callback(ticker, price)

            # Update signals with thread safety
            with self._lock:
                for ticker, signal_data in new_signals.items():
                    self._latest_signals[ticker] = signal_data
                    price_info = f" at ${signal_data['price']:.2f}" if signal_data['price'] is not None else " (price unavailable)"
                    logger.info(f"Updated news alert signal for {ticker} to {signal_data['flag'].name}{price_info}")

        except Exception as e:
            logger.error(f"Error querying ClickHouse news alerts: {e}")
            # Attempt to reconnect on next poll cycle if connection seems lost
            if "Connection timed out" in str(e) or "Not connected" in str(e):
                if self._client:
                    self._client.close()
                self._client = None

    def _run_schedule(self):
        """Runs the scheduled polling task."""
        # Schedule to run every 2 seconds for very frequent news alert checking
        schedule.every(2).seconds.do(self._poll_db)
        logger.info("Scheduled ClickHouse news alert polling every 2 seconds.")

        while not self._stop_event.is_set():
            schedule.run_pending()
            # Wait up to 1 second OR return immediately if the event is set
            woke_early = self._stop_event.wait(timeout=1.0)
            if woke_early:
                logger.debug("Stop event detected, exiting news alert polling loop.")
                break

        logger.info("ClickHouse news alert polling scheduler stopped.")

    def start_polling(self):
        """Starts the background polling thread."""
        if self._polling_thread is None or not self._polling_thread.is_alive():
            self._connect()  # Initial connection attempt
            self._stop_event.clear()
            self._polling_thread = threading.Thread(target=self._run_schedule, daemon=True)
            self._polling_thread.start()
            logger.info("ClickHouse news alert signal polling thread started.")
        else:
            logger.warning("News alert polling thread already running.")

    def stop_polling(self):
        """Signals the polling thread to stop."""
        if self._polling_thread and self._polling_thread.is_alive():
            logger.info("Stopping ClickHouse news alert signal polling thread...")
            self._stop_event.set()
            self._polling_thread.join(timeout=5)  # Wait for thread to finish
            if self._polling_thread.is_alive():
                logger.warning("News alert polling thread did not stop gracefully.")
            if self._client:
                self._client.close()
                logger.info("Closed ClickHouse connection for news alerts.")
        self._polling_thread = None

    def get_latest_signal(self, ticker: str) -> Optional[dict]:
        """
        Retrieves the latest signal detected for a specific ticker.

        :param ticker: The ticker symbol.
        :return: A dictionary {'flag': PriceDirection, 'timestamp': datetime} or None if no signal.
        """
        with self._lock:
            return self._latest_signals.get(ticker)

    def set_new_ticker_callback(self, callback: Callable[[str, float], None]) -> None:
        """
        Set callback for when new tickers are discovered in news alerts.
        
        :param callback: Function to call when a new ticker is found. 
                        Receives (ticker: str, price: float) where price may be None.
        """
        self._new_ticker_callback = callback
        logger.info("New ticker callback set for NewsAlertSignalProvider")

    def get_all_active_tickers(self) -> list[str]:
        """
        Get all tickers that currently have active signals.
        
        :return: List of ticker symbols with active signals
        """
        with self._lock:
            return list(self._latest_signals.keys())

    def add_ticker_to_watch_list(self, ticker: str) -> None:
        """
        Add a ticker to the monitoring list.
        
        :param ticker: Ticker symbol to add
        """
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info(f"Added {ticker} to news alert watch list")

    def clear_old_signals(self, max_age_minutes: int = 30):
        """
        Clear signals older than the specified age to prevent memory buildup.
        
        :param max_age_minutes: Maximum age of signals to keep (default: 30 minutes)
        """
        cutoff_time = datetime.now() - timedelta(minutes=max_age_minutes)
        
        with self._lock:
            tickers_to_remove = []
            for ticker, signal_data in self._latest_signals.items():
                if signal_data['timestamp'] < cutoff_time:
                    tickers_to_remove.append(ticker)
            
            for ticker in tickers_to_remove:
                del self._latest_signals[ticker]
                logger.debug(f"Cleared old news alert signal for {ticker}")


# Example Usage (for testing purposes)
if __name__ == '__main__':
    # Configure logging
    log_fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    logger.add("news_alert_signals.log", rotation="10 MB", level="DEBUG", format=log_fmt)

    logger.info("Starting NewsAlertSignalProvider example...")

    # Create a dummy .env file for testing if it doesn't exist
    if not os.path.exists("predictions/.env"):
        logger.warning("predictions/.env not found, creating dummy file for testing.")
        os.makedirs("predictions", exist_ok=True)
        with open("predictions/.env", "w") as f:
            f.write("CLICKHOUSE_HOST=localhost\n")
            f.write("CLICKHOUSE_HTTP_PORT=8123\n")
            f.write("CLICKHOUSE_USER=default\n")
            f.write("CLICKHOUSE_PASSWORD=\n")
            f.write("CLICKHOUSE_DATABASE=default\n")
            f.write("CLICKHOUSE_SECURE=false\n")
            
    # Test ticker configurations
    test_ticker_configs = [
        {'ticker': "AAPL", 'inverted': 'regular'},
        {'ticker': "TSLA", 'inverted': 'inverted'},  # Example: TSLA will have its signals inverted
    ]

    try:
        provider = NewsAlertSignalProvider(ticker_configs=test_ticker_configs, lookback_minutes=3)
        provider.start_polling()

        # Keep the main thread alive to let the polling thread run
        count = 0
        while count < 300:  # Run for 5 minutes for testing
            for t_config in test_ticker_configs:
                t = t_config['ticker']
                signal = provider.get_latest_signal(t)
                if signal:
                    logger.info(f"Main Thread: Latest news alert signal for {t}: {signal['flag']} (Timestamp: {signal['timestamp']})")
            time.sleep(10)
            count += 10

    except Exception as e:
        logger.exception(f"An error occurred during news alert example execution: {e}")
    finally:
        if 'provider' in locals():
            provider.stop_polling()
        logger.info("NewsAlertSignalProvider example finished.") 