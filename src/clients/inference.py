"""
Module inference.py handles prediction inference by communicating with the prediction server.

Classes:
    Inference: Manages prediction requests for specified tickers.
"""

from datetime import datetime
import os
from typing import List, Union

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from schema.prediction import Prediction


class MongerInference:
    """Manage predictions for a list of tickers.

    :param Union[str, None] base_url: Base URL of the prediction server. If None, loaded from .env.
    :param List[str] tickers: List of ticker symbols to predict.
    """

    def __init__(self, base_url: Union[str, None], tickers: List[str]):
        load_dotenv()
        if base_url is None:
            base_url = os.environ["PREDICTION_SERVER_URL"]

        self.base_url = base_url
        self.tickers = tickers

    async def predict(self, ticker: str, timestamp: datetime) -> Union[Prediction, None]:
        """Fetch prediction for a single ticker.

        :param str ticker: The ticker symbol to predict.
        :param datetime timestamp: The timestamp for the prediction.
        :return Union[Prediction, None]: The prediction result, or None if an error occurred.
        """

        async with aiohttp.ClientSession() as session:
            return await self._get_prediction(ticker, session, timestamp)

    async def _get_prediction(
        self, ticker: str, session: aiohttp.ClientSession, timestamp: Union[datetime, None] = None
    ) -> Union[Prediction, None]:
        """Get prediction for a single ticker.

        :param str ticker: The ticker symbol to predict.
        :param aiohttp.ClientSession session: The HTTP session to use for the request.
        :param Union[datetime, None] timestamp: The timestamp for the prediction. If None, it is
            determined automatically.
        :return Union[Prediction, None]: The prediction result, or None if an error occurred.
        """
        try:
            timestamp = self._get_time(ticker) if timestamp is None else timestamp
        except ValueError as e:
            logger.error(f"FAILED --- Prediction {timestamp}:{ticker} --- {e}")
            return None
        integer_time = self._convert_timestamp(timestamp)
        try:
            predictions = await self._fetch_prediction(ticker, integer_time, session)
            return predictions
        except Exception as e:
            logger.error(f"FAILED --- Prediction {timestamp}:{ticker} --- {e}")
            return None

    async def _fetch_prediction(
        self, ticker: str, integer_time: int, session: aiohttp.ClientSession
    ) -> Prediction:
        """Fetch prediction from the prediction server.

        :param str ticker: The ticker symbol to predict.
        :param int integer_time: The timestamp in milliseconds.
        :param aiohttp.ClientSession session: The HTTP session to use for the request.
        :return Prediction: The prediction result.
        :raises aiohttp.ClientError: If the HTTP request fails.
        """
        try:
            body = {"ticker": ticker, "timestamp": integer_time}
            async with session.post(f"{self.base_url}/inference", json=body) as response:
                response.raise_for_status()
                data = await response.json()
                return Prediction(**data)
        except aiohttp.ClientError as e:
            raise e

    def _convert_timestamp(self, timestamp: datetime) -> int:
        """Convert a datetime object to an integer timestamp in milliseconds.

        :param datetime timestamp: The datetime to convert.
        :return int: The timestamp in milliseconds.
        """
        return int(timestamp.timestamp() * 1000)
