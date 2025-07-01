"""
The brokerage module handles communication with the Interactive Brokers TWS API.

    For more information on implementing the TWS API, please refer to their documentation here:
https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/#api-introduction

For a step-by-step guide on installing the `ibapi` package, please review the detailed guide in
the Monger Notion wiki:
https://www.notion.so/IB-API-Source-Code-Setup-ba3636ee75c34e128250354612093405?pvs=4
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from ibapi.common import BarData, TickerId
from ibapi.contract import Contract
from ibapi.ticktype import TickType
from ibapi.wrapper import EWrapper
from loguru import logger

from logic import OrderExecutor
from schema import MarketData, Position, TraderAssignment
from schema.trade import Trade

if TYPE_CHECKING:
    from app import TradeMonger
    from portfolio_app import PortfolioManager

BID_ASK_REQ_ID = 1003
TRAILING_15_MIN_REQ_ID = 1004
POSITION_PNL_REQ_ID = 1002


class PortfolioWrapper(EWrapper):
    """
    The portfolio wrapper handles providing READ access to IBKR.

    This instance of the wrapper is charged with handling the portfolio and account
    level events.
    """

    def pnl(self, reqId: int, dailyPnL: float, unrealizedPnL: float, realizedPnL: float):
        # Debug logging only every 10 seconds to avoid spam
        if datetime.now().second % 10 == 0:
            logger.debug(
                f"Daily PnL: {dailyPnL}, Unrealized PnL: {unrealizedPnL}, "
                f" Realized PnL: {realizedPnL}"
            )
        
        # Calculate total PnL (realized + unrealized) for proper risk management
        total_pnl = realizedPnL + unrealizedPnL
        logger.debug(f"Total PnL for threshold check: {total_pnl} (Realized: {realizedPnL} + Unrealized: {unrealizedPnL})")
        
        # CRITICAL: Check PnL threshold on EVERY update, not just when logging
        self.check_pnl_threshold(float(total_pnl))


class MongerWrapper(EWrapper):
    """
    The MongerWrapper handles providing READ access to IBKR.

    Refer to the IBKR's Architecture Documentation for more information
    https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#architecture

    The wrapper is utilzied to listen to various events that will be emitted by TWS. These
    events can be grouped into 3 different categories:
        - Account and Portfolio
            - `accountDonwloadEnd`: Notifies when all the account's information has finished.
            - `updateAccountValue`: Notifies when an account value changes. This occurs every
                3 minutes or every time a position changes.
            - `updatePortfolio`: Notifies every time the position for the subscribed account and
                contract is changes.
        - Market Data
            - `tickSize`: Notifies when the tick size changes.
                - `tickID` == 0: Bid Size
                - `tickID` == 3: Ask Size
            - `tickPrice`: Notifies when the tick price changes.
                - `tickID` == 1: Bid Price
                - `tickID` == 2: Ask Price
        - Order Execution
            - `orderStatus`: Notifies when the order status changes.
            - `execDetails`: Notifies when an execution report is received.
            - `commissionReport`: Notifies when a commission report is received.

    :param OrderBook order_book: The thread-shared order book data structure.
    """

    order_executor: OrderExecutor
    market_data: MarketData

    def __init__(self, assignment: TraderAssignment, portfolio_manager: Optional["PortfolioManager"] = None, staggered_order_delay: float = 5.0):
        """Initialize the MongerWrapper."""
        EWrapper.__init__(self)

        self.assignment = assignment
        self.portfolio_manager = portfolio_manager
        self.market_data = MarketData(assignment=assignment)
        
        # Create Position object first, passing context
        context = {'portfolio_manager': self.portfolio_manager}
        position_obj = Position(assignment=assignment, model_config={"arbitrary_types_allowed": True}, **context)

        # Initialize the order executor, passing the created position_obj and staggered delay config
        self.order_executor = OrderExecutor(
            assignment=assignment, 
            position=position_obj, # Pass the created Position object
            market_data=self.market_data, 
            portfolio_manager=self.portfolio_manager,
            staggered_order_delay=staggered_order_delay
        )

    def initialize_executor(self, tws_app: "TradeMonger") -> None:
        # This method is now empty as the initialization logic is now handled in the __init__ method
        pass

    def tickPrice(self, reqId: TickerId, tickType: TickType, price: float, *args):
        """
        Handle tick price updates from IBKR.

        This method is triggered every time the BID or ASK price changes. It updates the
        shared `book_data` dictionary in a thread-safe manner using `book_lock`. By ensuring
        that only utilized tick types are processed, it avoids unnecessary computations and
        maintains the integrity of the shared data structure.

        Additionally, it logs the updated price information for monitoring and debugging
        purposes.

        :param TickerId reqId: The request ID which initiated the subscription.
        :param TickType tickType: The type of the price tick.
        :param float price: The updated price.
        :param Tickparamib paramib: Additional tick paramibutes.
        """
        self.market_data.order_book.update_price(tickType, price)
        self.order_executor.handle_market_data_update()

    def tickSize(self, reqId: TickerId, tickType: TickType, size: int):
        """
        Handle tick size updates from IBKR.

        This method is triggered every time the BID or ASK size changes. It updates the
        shared `book_data` dictionary in a thread-safe manner using `book_lock`. By ensuring
        that only utilized tick types are processed, it avoids unnecessary computations and
        maintains the integrity of the shared data structure.

        Additionally, it logs the updated size information for monitoring and debugging
        purposes.

        :param TickerId reqId: The request ID which initiated the subscription.
        :param TickType tickType: The type of the size tick.
        :param int size: The updated size.
        """
        self.market_data.order_book.update_size(tickType, size)

    def historicalData(
        self,
        reqId: int,
        bar: BarData,
    ):
        """
        Handle historical data updates from IBKR.

        This method is triggered every time historical data is received from IBKR. It logs the
        historical data for monitoring and debugging purposes.

        :param int reqId: The request ID which initiated the subscription.
        :param BarData bar: The historical data bar.
        """
        self.market_data.handle_bar(bar)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """
        Handle the end of historical data updates from IBKR.

        This method is triggered when the historical data request is completed. It logs the
        completion of the historical data request for monitoring and debugging purposes.

        :param int reqId: The request ID which initiated the subscription.
        :param str start: The start date of the historical data.
        :param str end: The end date of the historical data.
        """
        self.market_data.handle_bars_end(start, end)
        self.order_executor.handle_market_data_update()

    def orderStatus(
        self,
        orderId: int,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int,
        parentId: int,
        lastFillPrice: float,
        clientId: int,
        whyHeld: str,
        mktCapPrice: float,
    ):
        """
        Callback for order status updates.

        This method is called by TWS whenever the status of an order changes.
        It provides real-time updates on the order's execution status.

        :param orderId: The order ID that was specified previously in the call to placeOrder()
        :param status: The order status. Possible values include:
            PendingSubmit, PendingCancel, PreSubmitted, Submitted, ApiCancelled, Cancelled, Filled,
            Inactive
        :param filled: Number of shares or contracts filled
        :param remaining: Number of shares or contracts remaining to be filled
        :param avgFillPrice: Average filling price
        :param permId: The TWS id used to identify orders persistently
        :param parentId: Parent order Id, used for bracket and auto trailing stop orders
        :param lastFillPrice: Price at which the last shares/contracts were filled
        :param clientId: The ID of the client (or TWS) that placed the order
        :param whyHeld: This field is used to identify an order held when TWS is trying to locate
            shares for a short sell
        :param mktCapPrice: The market capitalization price
        """
        self.order_executor.handle_order_status(
            order_id=orderId,
            status=status,
            filled=filled,
            avg_fill_price=avgFillPrice,
            client_id=clientId,
        )

    def position(self, account: str, contract: Contract, position: Decimal, avgCost: float):
        """
        Callback for position updates.

        :param account: The account holding the position
        :param contract: The contract for which the position is held
        :param position: The number of shares or contracts held
        :param avgCost: The average cost of the position
        """
        ticker_symbol = contract.symbol
        position_val = float(position)
        logger.debug(f"[TWS Callback] Received position update for {ticker_symbol}: Size={position_val}, AvgCost={avgCost}, Account={account}")
        
        if hasattr(self, 'order_executor') and hasattr(self.order_executor, 'position'):
            logger.debug(f"[TWS Callback] Forwarding position update for {ticker_symbol} to Position object.")
            self.order_executor.position.handle_position_update(
                ticker=ticker_symbol,
                contract_id=contract.conId,
                position=position_val,
                avg_price=float(avgCost),
            )
        else:
             logger.error(f"[TWS Callback] Could not handle position update for {ticker_symbol}: order_executor or position not initialized.")

    def pnlSingle(
        self,
        reqId: int,
        pos: Decimal,
        dailyPnL: float,
        unrealizedPnL: float,
        realizedPnL: float,
        value: float,
    ):
        """
        Callback for P/L updates.
        """

        # Mask the unrealized P/L based on value field
        if value == 0:
            unrealizedPnL = 0

        self.order_executor.handle_pnl_update(realizedPnL, unrealizedPnL)
