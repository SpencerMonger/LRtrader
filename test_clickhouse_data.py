#!/usr/bin/env python3

import os
import sys
import clickhouse_connect
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add project root to path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)


def test_clickhouse_connection():
    """Test ClickHouse connection and query crossover events data"""

    # Load configuration from predictions/.env
    dotenv_path = os.path.join(os.path.dirname(__file__), "predictions", ".env")
    load_dotenv(dotenv_path=dotenv_path)

    db_host = os.getenv("CLICKHOUSE_HOST")
    db_port = int(os.getenv("CLICKHOUSE_HTTP_PORT", 8123))
    db_user = os.getenv("CLICKHOUSE_USER", "default")
    db_password = os.getenv("CLICKHOUSE_PASSWORD", "")
    db_database = os.getenv("CLICKHOUSE_DATABASE")
    db_secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() == "true"

    print(
        f"Connecting to ClickHouse: {db_host}:{db_port}, database={db_database}, secure={db_secure}"
    )

    try:
        client = clickhouse_connect.get_client(
            host=db_host,
            port=db_port,
            username=db_user,
            password=db_password,
            database=db_database,
            secure=db_secure,
        )

        # Test connection
        client.ping()
        print("✓ Successfully connected to ClickHouse")

        # Check table existence
        table_check_query = """
        SELECT count(*) as table_exists
        FROM system.tables
        WHERE database = 'vault42' AND name = 'crossover_events'
        """
        result = client.query(table_check_query)
        table_exists = result.result_rows[0][0] > 0

        if not table_exists:
            print("❌ Table vault42.crossover_events does not exist")
            return

        print("✓ Table vault42.crossover_events exists")

        # First, check table structure to see available columns
        describe_query = "DESCRIBE vault42.crossover_events"
        result = client.query(describe_query)

        print("\nTable structure:")
        print("Column Name | Type")
        print("-" * 30)
        for row in result.result_rows:
            col_name, col_type = row[0], row[1]
            print(f"{col_name:20} | {col_type}")

        # Check recent data
        tickers = ["AAPL", "META", "TSLA", "AMZN", "GOOG", "MSFT"]
        lookback_time = datetime.now() - timedelta(hours=24)

        recent_data_query = """
        SELECT
            ticker,
            event_type,
            created_at,
            count(*) as event_count
        FROM vault42.crossover_events
        WHERE ticker IN %(tickers)s
          AND created_at >= %(lookback_time)s
        GROUP BY ticker, event_type, created_at
        ORDER BY created_at DESC
        LIMIT 20
        """

        print("\nQuerying recent crossover events (last 24 hours)...")
        result = client.query(
            recent_data_query, parameters={"tickers": tickers, "lookback_time": lookback_time}
        )

        if result.result_rows:
            print(f"Found {len(result.result_rows)} recent events:")
            print("Ticker | Event Type | Timestamp | Count")
            print("-" * 50)
            for row in result.result_rows:
                ticker, event_type, timestamp, count = row
                print(f"{ticker:6} | {event_type:12} | {timestamp} | {count}")
        else:
            print("❌ No recent crossover events found in the last 24 hours")

        # Check overall data volume
        total_count_query = """
        SELECT
            ticker,
            count(*) as total_events,
            max(created_at) as latest_event
        FROM vault42.crossover_events
        WHERE ticker IN %(tickers)s
        GROUP BY ticker
        ORDER BY ticker
        """

        print("\nOverall data volume for configured tickers:")
        result = client.query(total_count_query, parameters={"tickers": tickers})

        if result.result_rows:
            print("Ticker | Total Events | Latest Event")
            print("-" * 45)
            for row in result.result_rows:
                ticker, total, latest = row
                print(f"{ticker:6} | {total:11} | {latest}")
        else:
            print("❌ No historical data found for any configured tickers")

        # Check data from last 5 minutes (what the system actually polls)
        recent_query = """
        SELECT
            ticker,
            event_type,
            created_at
        FROM vault42.crossover_events
        WHERE ticker IN %(tickers)s
          AND created_at >= %(lookback_time)s
        ORDER BY created_at DESC
        LIMIT 10
        """

        five_min_ago = datetime.now() - timedelta(minutes=5)
        print("\nChecking for events in last 5 minutes (what system polls):")
        result = client.query(
            recent_query, parameters={"tickers": tickers, "lookback_time": five_min_ago}
        )

        if result.result_rows:
            print("Recent events (last 5 minutes):")
            for row in result.result_rows:
                ticker, event_type, timestamp = row
                print(f"  {ticker}: {event_type} at {timestamp}")
        else:
            print("❌ No events in the last 5 minutes")

        client.close()

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    test_clickhouse_connection()
