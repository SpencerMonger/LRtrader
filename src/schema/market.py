"""
The market class is used to store the current state of the market.
"""

from datetime import datetime
from enum import Enum
import threading

from ibapi.common import BarData
from loguru import logger
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, computed_field
import pytz

from .enums import PositionSide
from .prediction import PriceDirection


class TickType(str, Enum):
    """
    The side of the order book.
    """

    BID = "bid"
    ASK = "ask"
    LAST = "last"


INT_TO_TICK_TYPE = {
    0: TickType.BID,
    1: TickType.BID,
    2: TickType.ASK,
    3: TickType.ASK,
    4: TickType.LAST,
}


class OrderBook(BaseModel):
    """
    The order book for a given contract.

    :param Optional[float] bid_size: The size of the bid.
    :param Optional[float] bid_price: The price of the bid.
    :param Optional[float] ask_size: The size of the ask.
    :param Optional[float] ask_price: The price of the ask.
    """

    _lock: threading.Lock = PrivateAttr(...)

    bid_size: float = 0.0
    bid_price: float = 0.0
    ask_size: float = 0.0
    ask_price: float = 0.0
    last_price: float = 0.0

    model_config: ConfigDict = {"arbitrary_types_allowed": True}

    def __init__(self, **data):
        super().__init__(**data)
        self._lock = threading.Lock()

    def update_size(self, tick_type: int, size: float):
        """
        Update the size of the order book.

        :param OrderSide side: The side of the order book (bid or ask).
        :param float size: The new size of the order book.
        """

        if tick_type not in INT_TO_TICK_TYPE:
            return

        side = INT_TO_TICK_TYPE.get(tick_type)

        with self._lock:
            if side == TickType.BID:
                self.bid_size = size
            elif side == TickType.ASK:
                self.ask_size = size

    def update_price(self, tick_type: int, price: float):
        """
        Update the price of the order book.

        :param OrderSide side: The side of the order book (bid or ask).
        :param float price: The new price of the order book.
        """
        if tick_type not in INT_TO_TICK_TYPE:
            return

        side = INT_TO_TICK_TYPE.get(tick_type)

        with self._lock:
            if side == TickType.BID:
                self.bid_price = price
            elif side == TickType.ASK:
                self.ask_price = price
            elif side == TickType.LAST:
                self.last_price = price

    def get_entry_price(self, prediction_flag: PriceDirection, best_spread: bool = True) -> float:
        """
        Get the entry price for the order book.

        :param PriceDirection prediction_flag: The flag of the prediction (BULLISH or BEARISH)
        :return float: The entry price.
        """
        with self._lock:
            if best_spread:
                return (
                    self.bid_price if prediction_flag == PriceDirection.BULLISH else self.ask_price
                )
            else:
                return (
                    self.ask_price if prediction_flag == PriceDirection.BULLISH else self.bid_price
                )

    def get_exit_price(self, position_side: PositionSide, best_spread: bool = True) -> float:
        """
        Get the entry price for the order book.

        :param PriceDirection prediction_flag: The flag of the prediction (BULLISH or BEARISH)
        :return float: The entry price.
        """
        with self._lock:
            if best_spread:
                return self.ask_price if position_side == PositionSide.LONG else self.bid_price
            else:
                return self.bid_price if position_side == PositionSide.LONG else self.ask_price


class MarketData(BaseModel):
    """
    The order book for a given contract.

    :param Optional[float] bid_size: The size of the bid.
    :param Optional[float] bid_price: The price of the bid.
    :param Optional[float] ask_size: The size of the ask.
    :param Optional[float] ask_price: The price of the ask.
    """

    _lock: threading.Lock = PrivateAttr(...)
    _bars: list[BarData] = PrivateAttr(default_factory=list)

    order_book: OrderBook = Field(..., default_factory=OrderBook)

    def __init__(self, **data):
        super().__init__(**data)
        self._lock = threading.Lock()

    @computed_field
    def last(self) -> float:
        """
        The last price of the market.
        """
        return self.order_book.last_price

    @computed_field
    def bid(self) -> float:
        """
        The current bid price.
        """
        return self.order_book.bid_price

    @computed_field
    def ask(self) -> float:
        """
        The current ask price.
        """
        return self.order_book.ask_price

    def _update_extrema(self) -> None:
        """Update local extrema if necessary."""
        if not self._extrema_need_update:
            return

        if len(self._bars) < 3:
            self._recent_minima = np.array([])
            self._recent_maxima = np.array([])
            return

        current_time = int(self._bars[-1].date)
        fifteen_min_ago = current_time - 900  # 15 minutes in seconds

        recent_bars = [bar for bar in self._bars if int(bar.date) >= fifteen_min_ago]
        if len(recent_bars) < 3:
            self._recent_minima = np.array([])
            self._recent_maxima = np.array([])
            return

        lows = np.array([bar.low for bar in recent_bars])
        highs = np.array([bar.high for bar in recent_bars])

        # Find local minima
        local_min = np.r_[True, lows[1:] < lows[:-1]] & np.r_[lows[:-1] < lows[1:], True]

        self._recent_minima = lows[local_min]

        # Find local maxima
        local_max = np.r_[True, highs[1:] > highs[:-1]] & np.r_[highs[:-1] > highs[1:], True]
        self._recent_maxima = highs[local_max]

        self._extrema_need_update = False

    @computed_field
    def trailing_15_min_local_minima(self) -> list[float]:
        """
        Get the stop loss for a long position.
        """
        return self._recent_minima.tolist()

    @computed_field
    def trailing_15_min_local_maxima(self) -> list[float]:
        """
        Get the stop loss for a long position.
        """
        return self._recent_maxima.tolist()

    def handle_bar(self, bar: BarData) -> None:
        """
        Update the size of the order book.

        :param OrderSide side: The side of the order book (bid or ask).
        """
        with self._lock:
            # Check if a bar with the same timestamp already exists
            if not any(existing_bar.date == bar.date for existing_bar in self._bars):
                self._bars.append(bar)

    def handle_bars_end(self, start: str, end: str) -> None:
        """
        Prune the order book to the last 15 minutes of data.
        """
        try:
            start_str = start.strip()
            # Split the string to remove the timezone part if present
            parts = start_str.split(' ')
            date_time_part = ' '.join(parts[:2]) # Assumes format 'YYYYMMDD HH:MM:SS TZ' or 'YYYYMMDD'

            start_dt = None
            # Try parsing with the full format first
            try:
                start_dt = datetime.strptime(date_time_part, "%Y%m%d %H:%M:%S")
            except ValueError as e_full:
                # If full format fails, try parsing just the date
                try:
                    # Use the first part only if it's just a date
                    start_dt = datetime.strptime(parts[0], "%Y%m%d")
                except ValueError as e_date:
                    # If both formats fail, re-raise the original error or a combined one
                    logger.error(f"Failed to parse start date string: '{start_str}' with both formats. Full: {e_full}, DateOnly: {e_date}")
                    raise ValueError(f"Could not parse date string: {start_str}") from e_date

            if start_dt is None:
                 # This should ideally not be reached if the logic above is correct, but as a safeguard:
                 raise ValueError(f"Date parsing resulted in None for input: {start_str}")

            # Create a timezone object
            eastern = pytz.timezone("US/Eastern")

            # Localize the naive datetime to US/Eastern
            start_dt_eastern = eastern.localize(start_dt)

            # Convert to UTC
            start_utc = start_dt_eastern.astimezone(pytz.UTC)

            # Convert to timestamp
            start_timestamp = int(start_utc.timestamp())

            with self._lock:
                # Remove all bars before the start timestamp
                self._bars = [bar for bar in self._bars if int(bar.date) >= start_timestamp]
                self._extrema_need_update = True
                self._update_extrema()

        except ValueError as e:
            logger.error(f"Error parsing datetime: {e}")
            # You might want to add some fallback behavior here

        except Exception as e:
            logger.error(f"Unexpected error in handle_bars_end: {e}")
