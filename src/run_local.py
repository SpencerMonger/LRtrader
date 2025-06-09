"""
The Monger Trading Algorithm
---------------------------
"""

import sys
import os

# Add the project root directory (one level up from 'src') to the Python path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

import argparse
import signal

import anyio
from anyio.from_thread import start_blocking_portal
from loguru import logger

from manager import MongerManager
from schema.assignment import AssignmentFactory

# Configure Loguru level
logger.remove() # Remove default handler
# Set level back to DEBUG
# logger.add(sys.stderr, level="DEBUG") # Add back stderr handler with DEBUG level
logger.add(sys.stderr, level="TRACE") # Add back stderr handler with TRACE level for detailed debugging

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Monger Trading Algorithm - Local Runner")
    parser.add_argument("--account", default="DUA725288")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7497, help="Port number (default: 7497)")
    parser.add_argument("--config", required=True, help="Path to the configuration YAML file")
    return parser.parse_args()


async def main() -> None:
    """
    The main entry point for the Monger Trading Algorithm.

    Initializes the MongerManager with a list of trader assignments, sets up signal handling
    for graceful shutdown, and starts the manager to begin trading operations.
    """
    args = parse_arguments()

    try:
        assignments = AssignmentFactory.create_assignments(args.config)
        if not assignments:
            logger.error("No valid assignments found in config file")
            return

        logger.info(f"Loaded {len(assignments)} assignments from config")
        for assignment in assignments:
            logger.info(f"Configured trader for {assignment.ticker}")

        manager = MongerManager(
            assignments, args.account, host=args.host, port=args.port, max_pnl=-50000
        )

        def signal_handler(signum, frame):
            # Use print instead of logger to avoid deadlock
            print("Interrupt received. Initiating graceful shutdown...")

            with start_blocking_portal(backend="asyncio") as portal:
                portal.call(manager.stop)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        await manager.start(auto_activate=True)
    except Exception as e:
        logger.error(f"Failed to start trading: {str(e)}")
    finally:
        if "manager" in locals():
            await manager.stop()


if __name__ == "__main__":
    anyio.run(main)
