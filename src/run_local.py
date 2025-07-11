"""
The Monger Trading Algorithm
---------------------------
"""

import sys
import os
from datetime import datetime

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

# Configure file logging with daily rotation and 5-day retention
log_file_path = f"logs/newstrader_run_{{time:YYYYMMDD}}.log"
logger.add(log_file_path, rotation="1 day", retention=5, level="TRACE", format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}", backtrace=True, diagnose=True)
logger.info("File logging configured. Log file: {}", log_file_path.format(time=datetime.now()))

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
        
        # Check if this is a global configuration with dynamic discovery
        config = AssignmentFactory.load_config(args.config)
        is_global_config = "global_defaults" in config
        signal_config = AssignmentFactory.create_signal_config(args.config)
        
        # Extract max_loss_cumulative from config to use as max_pnl
        position_config = config.get("position", {})
        max_loss_cumulative = position_config.get("max_loss_cumulative", 50000)  # Default fallback
        max_pnl = -abs(max_loss_cumulative)  # Convert to negative as expected by portfolio manager
        
        logger.info(f"Using max_loss_cumulative from config: ${max_loss_cumulative} (max_pnl: {max_pnl})")
        
        if is_global_config and signal_config.enable_dynamic_discovery:
            # For global configs with dynamic discovery, assignments start empty
            # Traders will be created dynamically when signals appear
            logger.info("Using global configuration with dynamic ticker discovery")
            logger.info("Starting with empty assignments - traders will be created dynamically from news alerts")
            if not assignments:
                assignments = []  # Start with empty list for dynamic discovery
        elif not assignments:
            logger.error("No valid assignments found in config file")
            return

        logger.info(f"Loaded {len(assignments)} static assignments from config")
        for assignment in assignments:
            logger.info(f"Configured trader for {assignment.ticker}")

        # Pass the config path to the manager for signal configuration
        manager = MongerManager(
            assignments, 
            args.account, 
            config_path=args.config,  # Pass config path for signal configuration
            host=args.host, 
            port=args.port, 
            max_pnl=max_pnl
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
