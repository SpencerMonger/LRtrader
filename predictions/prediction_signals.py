import os
import queue
import threading
import time
from datetime import datetime
from enum import Enum
from typing import Optional

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

class ClickhouseSignalProvider:
    """
    Polls a ClickHouse database for trading signals based on predicted values.
    """

    def __init__(self, ticker_configs: list[dict]):
        """
        Initializes the provider, loads configuration, and sets up state.

        :param ticker_configs: A list of dictionaries, where each dictionary
                               contains configuration for a ticker, including 'ticker' and 'inverted'.
                               Example: [{'ticker': 'AAPL', 'inverted': 'regular'}, ...]
        """
        self._ticker_configs = {config['ticker']: config for config in ticker_configs}
        self._tickers = [config['ticker'] for config in ticker_configs]
        self._load_config()
        self._latest_signals: dict[str, dict] = {}  # Stores {'ticker': {'flag': PriceDirection, 'timestamp': datetime}}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._polling_thread = None
        self._client = None

        # --- Update column and table names ---
        self._timestamp_col = "timestamp"  # Use 'timestamp' for ordering
        self._ticker_col = "ticker"            # Already correct
        self._value_col = "predicted_value"    # Assuming this is correct
        self._table_name = "stock_predictions" # Use plural form
        # --- End Placeholder Columns ---


    def _load_config(self):
        """Loads ClickHouse connection details from .env file."""
        dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        # Add log to check if file exists
        logger.debug(f"Looking for .env file at: {dotenv_path}, Exists: {os.path.exists(dotenv_path)}")
        # Check the return value of load_dotenv
        loaded_ok = load_dotenv(dotenv_path=dotenv_path)
        logger.debug(f"load_dotenv result: {loaded_ok}")

        self._db_host = os.getenv("CLICKHOUSE_HOST")
        # Hardcode the port to 8443
        # --- EDIT: Read port and secure flag from .env ---
        self._db_port = int(os.getenv("CLICKHOUSE_HTTP_PORT", 8123)) # Read HTTP port
        self._db_user = os.getenv("CLICKHOUSE_USER", "default")
        self._db_password = os.getenv("CLICKHOUSE_PASSWORD", "")
        self._db_database = os.getenv("CLICKHOUSE_DATABASE")
        # Read the secure flag, default to False if not set or invalid
        self._db_secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true" 
        # --- END EDIT ---

        # Update debug logging to reflect hardcoded value
        # --- EDIT: Update log message ---
        # logger.debug(f"CLICKHOUSE_PORT hardcoded to: {self._db_port}") 
        logger.debug(f"ClickHouse Connection Params: Host={self._db_host}, Port={self._db_port}, Secure={self._db_secure}")
        # --- END EDIT ---

        if not all([self._db_host, self._db_database]):
            raise ValueError("Missing required ClickHouse connection details in predictions/.env (CLICKHOUSE_HOST, CLICKHOUSE_DATABASE)")
        logger.info("ClickHouse configuration loaded.")

    def _connect(self):
        """Establishes connection to ClickHouse."""
        try:
            if self._client:
                self._client.close() # Close existing connection if any
            # Add debug logging for port used in connection attempt
            logger.debug(f"Attempting ClickHouse connection to {self._db_host}:{self._db_port} (secure: {self._db_secure})") # Use loaded secure flag
            self._client = clickhouse_connect.get_client(
                host=self._db_host,
                port=self._db_port, # Uses the value loaded from .env
                username=self._db_user,
                password=self._db_password,
                database=self._db_database,
                # --- EDIT: Use loaded secure flag ---
                # secure= (self._db_port == 443 or self._db_port == 8443) # Basic check for secure port
                secure=self._db_secure # Use the flag read from .env
                # --- END EDIT ---
            )
            self._client.ping()
            logger.info("Successfully connected to ClickHouse.")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse: {e}")
            self._client = None # Ensure client is None if connection failed


    def _poll_db(self):
        """Queries the database for the latest signal for each ticker."""
        if not self._client:
            logger.warning("No ClickHouse connection, attempting to reconnect.")
            self._connect()
            if not self._client:
                logger.error("Skipping DB poll due to connection failure.")
                return # Skip polling if connection failed

        logger.debug(f"Polling ClickHouse for signals for tickers: {self._tickers}")
        new_signals = {}

        # Construct the query to get the latest row for each ticker among the configured ones
        # Using a subquery with row_number() is a common way to get the latest per group
        query = f"""
        SELECT
            {self._ticker_col},
            {self._value_col},
            {self._timestamp_col},
            pattern_label
        FROM (
            SELECT
                {self._ticker_col},
                {self._value_col},
                {self._timestamp_col},
                pattern_label,
                row_number() OVER (PARTITION BY {self._ticker_col} ORDER BY {self._timestamp_col} DESC) as rn
            FROM {self._table_name}
            WHERE {self._ticker_col} IN %(tickers)s
        )
        WHERE rn = 1
        """
        
        try:
            result = self._client.query(query, parameters={'tickers': self._tickers})
            
            current_time = datetime.now() # Use a consistent timestamp for this batch

            for row in result.result_rows:
                ticker, value, timestamp, pattern_label = row[0], row[1], row[2], row[3] # <-- ADDED: Unpack pattern_label

                signal_flag = None
                
                if value is not None:
                    # Bullish signal: keep existing conditions (ignore no_pattern and breakout)
                    ignored_patterns_bullish = {'no_pattern', 'breakout', 'doji_star', 'star', 'hammer', 'inverted_hammer', 'bearish_harami', 'bullish_engulfing', 'doji', 'morning_star', 'evening_star', 'dark_cloud_cover'}
                    if pattern_label not in ignored_patterns_bullish and 2.7 <= value <= 5.0:
                        signal_flag = PriceDirection.BULLISH
                    
                    # Bearish signal: specifically when pattern is "no_pattern" and in bearish range
                    elif pattern_label == 'no_pattern' and 0.1 <= value <= 2.1:
                        signal_flag = PriceDirection.BEARISH

                if signal_flag:
                    # Check inversion status for the ticker
                    ticker_config = self._ticker_configs.get(ticker)
                    if ticker_config and ticker_config.get('inverted') == 'inverted':
                        if signal_flag == PriceDirection.BULLISH:
                            signal_flag = PriceDirection.BEARISH
                            logger.debug(f"Inverting signal for {ticker} from BULLISH to BEARISH")
                        elif signal_flag == PriceDirection.BEARISH:
                            signal_flag = PriceDirection.BULLISH
                            logger.debug(f"Inverting signal for {ticker} from BEARISH to BULLISH")
                    
                    new_signals[ticker] = {'flag': signal_flag, 'timestamp': current_time}
                    logger.debug(f"Signal found for {ticker}: {signal_flag.name} (Value: {value}, DB Time: {timestamp})")

            with self._lock:
                # Always update the signal data if a valid signal was found in this poll
                for ticker, signal_data in new_signals.items():
                    # Log whether it's an update or the same signal reappearing
                    log_prefix = "Updated"
                    if ticker in self._latest_signals and self._latest_signals[ticker]['flag'] == signal_data['flag']:
                        log_prefix = "Reconfirmed"
                    
                    self._latest_signals[ticker] = signal_data # Store the latest data (incl. timestamp)
                    logger.info(f"{log_prefix} signal for {ticker} to {signal_data['flag'].name}")

        except Exception as e:
            logger.error(f"Error querying ClickHouse: {e}")
            # Attempt to reconnect on next poll cycle if connection seems lost
            if "Connection timed out" in str(e) or "Not connected" in str(e):
                 self._client.close()
                 self._client = None


    def _run_schedule(self):
        """Runs the scheduled polling task."""
        # Schedule to run every minute at 12 seconds past the minute
        schedule.every().minute.at(":12").do(self._poll_db)
        logger.info("Scheduled ClickHouse polling every minute at :11 seconds.")

        while not self._stop_event.is_set():
            schedule.run_pending()
            # Replace time.sleep(1) with event.wait(1.0)
            # This waits up to 1 second OR returns immediately if the event is set.
            woke_early = self._stop_event.wait(timeout=1.0)
            if woke_early:
                 logger.debug("Stop event detected, exiting polling loop.")
                 break # Exit loop immediately if event was set

        logger.info("ClickHouse polling scheduler stopped.")


    def start_polling(self):
        """Starts the background polling thread."""
        if self._polling_thread is None or not self._polling_thread.is_alive():
            self._connect() # Initial connection attempt
            self._stop_event.clear()
            self._polling_thread = threading.Thread(target=self._run_schedule, daemon=True)
            self._polling_thread.start()
            logger.info("ClickHouse signal polling thread started.")
        else:
            logger.warning("Polling thread already running.")


    def stop_polling(self):
        """Signals the polling thread to stop."""
        if self._polling_thread and self._polling_thread.is_alive():
            logger.info("Stopping Clickhouse signal polling thread...")
            self._stop_event.set()
            self._polling_thread.join(timeout=5) # Wait for thread to finish
            if self._polling_thread.is_alive():
                 logger.warning("Polling thread did not stop gracefully.")
            if self._client:
                 self._client.close()
                 logger.info("Closed ClickHouse connection.")
        self._polling_thread = None


    def get_latest_signal(self, ticker: str) -> Optional[dict]:
        """
        Retrieves the latest signal detected for a specific ticker.

        :param ticker: The ticker symbol.
        :return: A dictionary {'flag': PriceDirection, 'timestamp': datetime} or None if no signal.
        """
        with self._lock:
            return self._latest_signals.get(ticker)

# Example Usage (for testing purposes)
if __name__ == '__main__':
    # Configure logging
    log_fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    logger.add("prediction_signals.log", rotation="10 MB", level="DEBUG", format=log_fmt)

    logger.info("Starting ClickhouseSignalProvider example...")

    # Create a dummy .env file for testing if it doesn't exist
    if not os.path.exists("predictions/.env"):
        logger.warning("predictions/.env not found, creating dummy file for testing.")
        os.makedirs("predictions", exist_ok=True)
        with open("predictions/.env", "w") as f:
            f.write("CLICKHOUSE_HOST=localhost\n")
            f.write("CLICKHOUSE_PORT=8123\n")
            f.write("CLICKHOUSE_USER=default\n")
            f.write("CLICKHOUSE_PASSWORD=\n")
            f.write("CLICKHOUSE_DATABASE=default\n")
            
    # --- Replace with tickers from your config ---
    test_ticker_configs = [
        {'ticker': "AAPL", 'inverted': 'regular'},
        {'ticker': "GOOG", 'inverted': 'inverted'} # Example: GOOG will have its signals inverted
    ]
    # ---

    try:
        provider = ClickhouseSignalProvider(tickers=test_ticker_configs)
        provider.start_polling()

        # Keep the main thread alive to let the polling thread run
        count = 0
        while count < 300 : # Run for 5 minutes for testing
            for t_config in test_ticker_configs:
                t = t_config['ticker']
                signal = provider.get_latest_signal(t)
                if signal:
                    logger.info(f"Main Thread: Latest signal for {t}: {signal['flag']} (Timestamp: {signal['timestamp']})")
            time.sleep(10)
            count += 10

    except Exception as e:
        logger.exception(f"An error occurred during example execution: {e}")
    finally:
        if 'provider' in locals():
            provider.stop_polling()
        logger.info("ClickhouseSignalProvider example finished.") 
