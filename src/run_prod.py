"""
The Monger Trading Algorithm - Production Runner
------------------------------------------------
"""

import argparse
import asyncio
from datetime import datetime, time, timedelta
import signal

import anyio
from loguru import logger
import pytz
import schedule

from manager import MongerManager
from schema.assignment import AssignmentFactory


# Global variables
manager = None
assignments = []
host = ""
port = 0
shutdown_flag = False

est_tz = pytz.timezone("America/New_York")

# Morning trading session (EST)
MORNING_START = est_tz.localize(datetime.combine(datetime.today(), time(10, 2))).timetz()
MORNING_ACTIVE = est_tz.localize(datetime.combine(datetime.today(), time(10, 3))).timetz()
# 10:00 AM est -- something goes wrong
MORNING_INACTIVE = est_tz.localize(datetime.combine(datetime.today(), time(10, 30))).timetz()
MORNING_END = est_tz.localize(datetime.combine(datetime.today(), time(10, 35))).timetz()

# Afternoon trading session (EST)
AFTERNOON_START = est_tz.localize(datetime.combine(datetime.today(), time(14, 59))).timetz()
AFTERNOON_ACTIVE = est_tz.localize(datetime.combine(datetime.today(), time(15, 0))).timetz()
AFTERNOON_INACTIVE = est_tz.localize(datetime.combine(datetime.today(), time(16, 0))).timetz()
AFTERNOON_END = est_tz.localize(datetime.combine(datetime.today(), time(16, 5))).timetz()


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Monger Trading Algorithm - Production Runner")
    parser.add_argument("--account", default="DUA725288")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7497, help="Port number (default: 7497)")
    parser.add_argument("--config", required=True, help="Path to the configuration YAML file")
    return parser.parse_args()


# State Management
class TradingState:
    def __init__(self):
        self.current_state = "Inactive"
        self.state_start_time = datetime.now(est_tz)
        self.next_state_change_time = self.get_next_state_change(self.current_state)
        logger.debug(
            f"Initialized TradingState with state '{self.current_state}' "
            f"and next_change at {self.next_state_change_time}"
        )

    def update_state(self, new_state: str):
        logger.debug(f"Updating state from '{self.current_state}' to '{new_state}'")
        self.current_state = new_state
        self.state_start_time = datetime.now(est_tz)
        self.next_state_change_time = self.get_next_state_change(new_state)
        logger.debug(
            f"New state '{self.current_state}' with next_change at {self.next_state_change_time}"
        )

    @staticmethod
    def is_weekday_on_date(date: datetime.date) -> bool:
        """Check if the given date is a weekday."""
        return date.weekday() < 5

    def get_next_state_change(self, state: str) -> datetime:
        now = datetime.now(est_tz)
        today = now.date()
        times = []

        if state == "Inactive":
            # Next change should be to Start (morning warm-up)
            times = [MORNING_START, AFTERNOON_START]
        elif state == "Warmup":
            # After warm-up, transition to Active
            times = [MORNING_ACTIVE, AFTERNOON_ACTIVE]
        elif state == "Active":
            # After active trading, transition to Inactive
            times = [MORNING_INACTIVE, AFTERNOON_INACTIVE]
        elif state == "Cooldown":
            # After cooldown, transition to Inactive
            times = [MORNING_END, AFTERNOON_END]
        else:
            logger.warning(f"Unknown state '{state}'. No state change times mapped.")
            return now

        future_times = []
        for t in times:
            # Create a naive datetime and then localize it to EST
            scheduled_time = datetime.combine(today, t)
            if scheduled_time > now:
                future_times.append(scheduled_time)

        if future_times:
            next_change = min(future_times)
            logger.debug(f"Next state change for state '{state}' is at {next_change}")
            return next_change
        else:
            # Schedule the first time tomorrow
            tomorrow = today + timedelta(days=1)
            # Find the next valid weekday if tomorrow is a weekend
            while not self.is_weekday_on_date(tomorrow):
                tomorrow += timedelta(days=1)
            scheduled_time = datetime.combine(tomorrow, times[0])
            logger.debug(
                f"No future times today for state '{state}'. "
                f"Scheduling next change at {scheduled_time}"
            )
            return scheduled_time


# Initialize Trading State
trading_state = TradingState()


def is_weekday():
    """Check if today is a weekday."""
    return datetime.now(est_tz).weekday() < 5


def is_weekday_on_date(date: datetime.date) -> bool:
    """Check if the given date is a weekday."""
    return date.weekday() < 5


def set_active_flag(active: bool):
    """Set the active flag for all TradeMonger instances."""
    global manager
    for trader in manager.mongers:
        trader.set_active(active)


async def start_trading_session():
    """Start the trading session."""
    global manager
    if is_weekday():
        logger.info("Warming up for trading session.")
        trading_state.update_state("Warmup")
        await manager.start()


def activate_trading():
    """Activate trading."""
    if is_weekday():
        logger.info("Activating trading session.")
        set_active_flag(True)
        trading_state.update_state("Active")


def deactivate_trading():
    """Deactivate trading."""
    if is_weekday():
        logger.info("Trading over. Cooling down and clearing positions.")
        set_active_flag(False)
        trading_state.update_state("Cooldown")


async def end_trading_session():
    """End the trading session."""
    global manager
    if is_weekday():
        logger.info("Shutting off trading session.")
        set_active_flag(False)
        trading_state.update_state("Inactive")
        await manager.stop()


def log_system_status():
    """Log the current system status."""
    now = datetime.now(est_tz)
    elapsed = now - trading_state.state_start_time
    if trading_state.next_state_change_time:
        until_next_change = trading_state.next_state_change_time - now
        # Ensure that the timedelta is not negative
        until_next_change = max(until_next_change, timedelta(seconds=0))
    else:
        until_next_change = timedelta(seconds=0)
    logger.info(
        f"Current Activity: {trading_state.current_state} | "
        f"Time Elapsed: {str(elapsed).split('.')[0]} | "
        f"Time Until Next Change: {str(until_next_change).split('.')[0]}"
    )


def schedule_tasks():
    """Schedule all tasks."""

    # Clear existing scheduled jobs to prevent duplication
    schedule.clear()

    # Manage the morning sessions
    schedule.every().day.at(MORNING_START.strftime("%H:%M")).do(
        lambda: asyncio.create_task(start_trading_session())
    ).tag("warmup_morning")
    schedule.every().day.at(MORNING_ACTIVE.strftime("%H:%M")).do(activate_trading).tag(
        "activate_morning"
    )
    schedule.every().day.at(MORNING_INACTIVE.strftime("%H:%M")).do(deactivate_trading).tag(
        "deactivate_morning"
    )
    schedule.every().day.at(MORNING_END.strftime("%H:%M")).do(
        lambda: asyncio.create_task(end_trading_session())
    ).tag("cooldown_morning")

    # Manage the afternoon sessions
    schedule.every().day.at(AFTERNOON_START.strftime("%H:%M")).do(
        lambda: asyncio.create_task(start_trading_session())
    ).tag("warmup_afternoon")
    schedule.every().day.at(AFTERNOON_ACTIVE.strftime("%H:%M")).do(activate_trading).tag(
        "activate_afternoon"
    )
    schedule.every().day.at(AFTERNOON_INACTIVE.strftime("%H:%M")).do(deactivate_trading).tag(
        "deactivate_afternoon"
    )
    schedule.every().day.at(AFTERNOON_END.strftime("%H:%M")).do(
        lambda: asyncio.create_task(end_trading_session())
    ).tag("cooldown_afternoon")

    # Schedule the system status logger to run every minute
    schedule.every(1).minutes.do(log_system_status).tag("status_logger")


async def run_scheduler():
    """Run the scheduler."""
    global shutdown_flag
    log_system_status()
    while not shutdown_flag:
        schedule.run_pending()
        await asyncio.sleep(1)


async def main():
    """
    The main entry point for the Monger Trading Algorithm in production.
    """
    global manager, assignments, host, port, shutdown_flag

    args = parse_arguments()

    host = args.host
    port = args.port

    logger.debug(f"Connecting to TWS @ {host}:{port}")

    try:
        assignments = AssignmentFactory.create_assignments(args.config)
        if not assignments:
            logger.error("No valid assignments found in config file")
            return

        logger.info(f"Loaded {len(assignments)} assignments from config")
        for assignment in assignments:
            logger.info(f"Configured trader for {assignment.ticker}")

        manager = MongerManager(assignments, args.account, host=host, port=port, max_pnl=-15000)

        def signal_handler(signum, frame):
            global shutdown_flag
            logger.info("Interrupt received. Initiating graceful shutdown...")
            shutdown_flag = True

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        schedule_tasks()
        await run_scheduler()

    except Exception as e:
        logger.error(f"Failed to start trading: {str(e)}")
    finally:
        if manager:
            await manager.stop()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    anyio.run(main)
