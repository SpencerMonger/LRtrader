#!/usr/bin/env python3
"""
Test script to insert mock crossover signals into the crossover_events table.
Uses actual tickers to ensure the trading system works correctly.
"""

import sys
import os
import uuid
from datetime import datetime, timedelta
from typing import List, Dict

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

import clickhouse_connect
from dotenv import load_dotenv
from loguru import logger

# Configure logging
log_fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
logger.add("test_mock_crossover_signals.log", rotation="10 MB", level="DEBUG", format=log_fmt)


class MockCrossoverSignalGenerator:
    """Generates and inserts mock crossover signals for testing purposes."""

    def __init__(self):
        self.client = None
        self._load_config()
        self._connect()

        # Use GOOG as example ticker
        self.test_tickers = ["GOOG"]

    def _load_config(self):
        """Load ClickHouse connection details from .env file."""
        dotenv_path = os.path.join(project_root, "predictions", ".env")
        logger.debug(f"Looking for .env file at: {dotenv_path}")

        if not os.path.exists(dotenv_path):
            logger.warning("predictions/.env not found, creating dummy file for testing.")
            os.makedirs(os.path.dirname(dotenv_path), exist_ok=True)
            with open(dotenv_path, "w") as f:
                f.write("CLICKHOUSE_HOST=localhost\n")
                f.write("CLICKHOUSE_HTTP_PORT=8123\n")
                f.write("CLICKHOUSE_USER=default\n")
                f.write("CLICKHOUSE_PASSWORD=\n")
                f.write("CLICKHOUSE_DATABASE=vault42\n")
                f.write("CLICKHOUSE_SECURE=false\n")

        load_dotenv(dotenv_path=dotenv_path)

        self.db_host = os.getenv("CLICKHOUSE_HOST", "localhost")
        self.db_port = int(os.getenv("CLICKHOUSE_HTTP_PORT", 8123))
        self.db_user = os.getenv("CLICKHOUSE_USER", "default")
        self.db_password = os.getenv("CLICKHOUSE_PASSWORD", "")
        self.db_database = os.getenv("CLICKHOUSE_DATABASE", "vault42")
        self.db_secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"

        logger.info(f"ClickHouse Config: {self.db_host}:{self.db_port}, DB: {self.db_database}")

    def _connect(self):
        """Establish connection to ClickHouse."""
        try:
            self.client = clickhouse_connect.get_client(
                host=self.db_host,
                port=self.db_port,
                username=self.db_user,
                password=self.db_password,
                database=self.db_database,
                secure=self.db_secure,
            )
            self.client.ping()
            logger.info("Successfully connected to ClickHouse.")
        except Exception as e:
            logger.error(f"Failed to connect to ClickHouse: {e}")
            raise

    def create_mock_signals(self, count: int = 20) -> List[Dict]:
        """Create a list of mock crossover signals."""
        signals = []
        base_time = datetime.now() - timedelta(minutes=30)

        event_types = ["bullish_cross", "bearish_cross"]

        for i in range(count):
            ticker = self.test_tickers[i % len(self.test_tickers)]
            event_type = event_types[i % 2]  # Alternate between bullish and bearish

            # Generate realistic price data
            price_before = 750.0 + (i * 5)  # Vary prices slightly
            price_after = price_before + (2.0 if event_type == "bullish_cross" else -2.0)

            # Generate signals over the last 30 minutes
            event_timestamp = base_time + timedelta(minutes=i * 1.5)

            signal = {
                "id": str(uuid.uuid4()),
                "ticker": ticker,
                "event_type": event_type,
                "event_timestamp": event_timestamp,
                "price_before": price_before,
                "price_after": price_after,
                "signal_before": 2.5 + (i * 0.1),  # Mock signal values
                "signal_after": 3.0 + (i * 0.1),
                "price_change": price_after - price_before,
                "signal_strength": 0.8 + (i * 0.01),
                "created_at": datetime.now(),  # Add created_at timestamp
            }

            signals.append(signal)

        return signals

    def insert_mock_signals(self, signals: List[Dict]):
        """Insert mock signals into the crossover_events table."""
        try:
            # Prepare data for insertion
            data = []
            for signal in signals:
                data.append(
                    [
                        signal["id"],
                        signal["ticker"],
                        signal["event_type"],
                        signal["event_timestamp"],
                        signal["price_before"],
                        signal["price_after"],
                        signal["signal_before"],
                        signal["signal_after"],
                        signal["price_change"],
                        signal["signal_strength"],
                        signal["created_at"],
                    ]
                )

            # Insert into the crossover_events table
            self.client.insert(
                table="vault42.crossover_events",
                data=data,
                column_names=[
                    "id",
                    "ticker",
                    "event_type",
                    "event_timestamp",
                    "price_before",
                    "price_after",
                    "signal_before",
                    "signal_after",
                    "price_change",
                    "signal_strength",
                    "created_at",
                ],
            )

            logger.info(
                f"Successfully inserted {len(signals)} mock signals into crossover_events table"
            )

            # Log some sample signals
            for signal in signals[:5]:
                logger.info(
                    f"Mock Signal: {signal['ticker']} - {signal['event_type']} at {signal['event_timestamp']}"
                )

        except Exception as e:
            logger.error(f"Failed to insert mock signals: {e}")
            raise

    def verify_signals(self) -> int:
        """Verify that signals were inserted and return count."""
        try:
            # Query recent signals
            query = """
            SELECT ticker, event_type, event_timestamp, price_before, price_after
            FROM vault42.crossover_events
            WHERE event_timestamp >= %(cutoff_time)s
            ORDER BY event_timestamp DESC
            LIMIT 50
            """

            cutoff_time = datetime.now() - timedelta(hours=1)
            result = self.client.query(query, parameters={"cutoff_time": cutoff_time})

            count = len(result.result_rows)
            logger.info(f"Found {count} recent crossover signals in the database")

            # Display some sample signals
            logger.info("Sample signals from database:")
            for i, row in enumerate(result.result_rows[:10]):
                ticker, event_type, timestamp, price_before, price_after = row
                logger.info(
                    f"  {i+1}. {ticker}: {event_type} at {timestamp} (${price_before:.2f} -> ${price_after:.2f})"
                )

            return count

        except Exception as e:
            logger.error(f"Failed to verify signals: {e}")
            return 0

    def cleanup_old_signals(self, hours_old: int = 24):
        """Clean up old test signals."""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours_old)

            # Count signals to be deleted
            count_query = """
            SELECT COUNT(*)
            FROM vault42.crossover_events
            WHERE event_timestamp < %(cutoff_time)s
            AND ticker IN %(test_tickers)s
            """

            count_result = self.client.query(
                count_query,
                parameters={"cutoff_time": cutoff_time, "test_tickers": self.test_tickers},
            )

            count_to_delete = count_result.result_rows[0][0] if count_result.result_rows else 0

            if count_to_delete > 0:
                # Delete old signals
                delete_query = """
                DELETE FROM vault42.crossover_events
                WHERE event_timestamp < %(cutoff_time)s
                AND ticker IN %(test_tickers)s
                """

                self.client.command(
                    delete_query,
                    parameters={"cutoff_time": cutoff_time, "test_tickers": self.test_tickers},
                )

                logger.info(f"Cleaned up {count_to_delete} old test signals")
            else:
                logger.info("No old test signals to clean up")

        except Exception as e:
            logger.warning(f"Failed to cleanup old signals: {e}")

    def close(self):
        """Close the database connection."""
        if self.client:
            self.client.close()
            logger.info("Closed ClickHouse connection")


def test_signal_insertion():
    """Test inserting mock crossover signals."""
    logger.info("=" * 60)
    logger.info("TESTING MOCK CROSSOVER SIGNAL INSERTION")
    logger.info("=" * 60)

    generator = None
    try:
        # Initialize generator
        generator = MockCrossoverSignalGenerator()

        # Clean up old signals first
        logger.info("Cleaning up old test signals...")
        generator.cleanup_old_signals(hours_old=1)  # Clean signals older than 1 hour

        # Create mock signals
        logger.info("Creating mock crossover signals...")
        mock_signals = generator.create_mock_signals(count=15)

        logger.info(f"Generated {len(mock_signals)} mock signals:")
        for signal in mock_signals:
            logger.info(
                f"  {signal['ticker']}: {signal['event_type']} at {signal['event_timestamp']}"
            )

        # Insert signals
        logger.info("Inserting mock signals into database...")
        generator.insert_mock_signals(mock_signals)

        # Verify insertion
        logger.info("Verifying signal insertion...")
        count = generator.verify_signals()

        if count >= len(mock_signals):
            logger.info("✅ SUCCESS: Mock signals inserted and verified!")
            return True
        else:
            logger.error("❌ FAILURE: Signal count mismatch after insertion")
            return False

    except Exception as e:
        logger.exception(f"Test failed with exception: {e}")
        return False
    finally:
        if generator:
            generator.close()


def test_signal_patterns():
    """Test creating signals with specific patterns for trading system testing."""
    logger.info("=" * 60)
    logger.info("TESTING SIGNAL PATTERNS FOR TRADING SYSTEM")
    logger.info("=" * 60)

    generator = None
    try:
        generator = MockCrossoverSignalGenerator()

        # Create signals with specific patterns
        patterns = [
            # Strong bullish signals for META
            {"ticker": "META", "event_type": "bullish_cross", "price_jump": 5.0, "strength": 0.95},
            {"ticker": "META", "event_type": "bullish_cross", "price_jump": 3.0, "strength": 0.85},
            # Strong bearish signals for TEST_BEARISH
            {
                "ticker": "TEST_BEARISH",
                "event_type": "bearish_cross",
                "price_jump": -4.0,
                "strength": 0.90,
            },
            {
                "ticker": "TEST_BEARISH",
                "event_type": "bearish_cross",
                "price_jump": -2.5,
                "strength": 0.80,
            },
            # Mixed signals for TEST_TREND
            {
                "ticker": "TEST_TREND",
                "event_type": "bullish_cross",
                "price_jump": 2.0,
                "strength": 0.75,
            },
            {
                "ticker": "TEST_TREND",
                "event_type": "bearish_cross",
                "price_jump": -1.5,
                "strength": 0.70,
            },
        ]

        signals = []
        base_time = datetime.now() - timedelta(minutes=10)

        for i, pattern in enumerate(patterns):
            signal = {
                "id": str(uuid.uuid4()),
                "ticker": pattern["ticker"],
                "event_type": pattern["event_type"],
                "event_timestamp": base_time + timedelta(minutes=i * 1.5),
                "price_before": 750.0,
                "price_after": 750.0 + pattern["price_jump"],
                "signal_before": 2.5,
                "signal_after": 2.5 + (pattern["price_jump"] * 0.1),
                "price_change": pattern["price_jump"],
                "signal_strength": pattern["strength"],
            }
            signals.append(signal)

        logger.info("Inserting pattern-based signals...")
        generator.insert_mock_signals(signals)

        # Verify
        count = generator.verify_signals()
        logger.info(
            f"✅ Inserted {len(signals)} pattern-based signals, total recent signals: {count}"
        )

        return True

    except Exception as e:
        logger.exception(f"Pattern test failed: {e}")
        return False
    finally:
        if generator:
            generator.close()


def main():
    """Insert mock crossover signals."""
    logger.info("Inserting mock crossover signals...")

    generator = None
    try:
        generator = MockCrossoverSignalGenerator()

        # Create and insert 1 mock signal
        mock_signals = generator.create_mock_signals(count=1)
        generator.insert_mock_signals(mock_signals)

        logger.info(f"✅ Successfully inserted {len(mock_signals)} mock crossover signals")

    except Exception as e:
        logger.error(f"❌ Failed to insert mock signals: {e}")
    finally:
        if generator:
            generator.close()


if __name__ == "__main__":
    main()
