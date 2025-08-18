"""
The predicate module contains interfaces and implementations for applying predicates to operations.
"""

from abc import abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, Field
from typing_extensions import Generic

from schema import Position, Prediction, PriceDirection
from schema.assignment import TraderAssignment
import logging


TStateObject = TypeVar("StateObject", bound=BaseModel)

logger = logging.getLogger(__name__)


class BaseBooleanPredicate(BaseModel, Generic[TStateObject]):
    """
    Interface to apply a boolean predicate to an operation.

    :param TraderAssignment assignment: The trader assignment configuration.
    """

    assignment: TraderAssignment = Field(...)

    @abstractmethod
    def apply(self, data: TStateObject, **context: Any) -> bool:
        """
        Apply the predicate to an operation.

        :param TStateObject data: The data to evaluate.
        :param Any context: Additional context.
        :return bool: True if the predicate is satisfied, False otherwise.
        """
        raise NotImplementedError


class BaseFloatPredicate(BaseModel, Generic[TStateObject]):
    """
    Interface for applying a float predicate to an operation.

    :param TraderAssignment assignment: The trader assignment configuration.
    """

    assignment: TraderAssignment = Field(...)

    @abstractmethod
    def apply(self, data: TStateObject, **context: Any) -> float:
        """
        Apply the predicate to an operation.

        :param TStateObject data: The data to evaluate.
        :param Any context: Additional context.
        :return float: The scaling factor for the operation.
        """
        raise NotImplementedError


class ConfidenceThresholdPredicate(BaseBooleanPredicate[Prediction]):
    """
    Predicate that checks if the prediction confidence falls within defined thresholds.
    """

    def apply(self, prediction: Prediction = None, **context: Any) -> bool:
        """
        Apply the confidence threshold predicate to a prediction.

        :param Prediction prediction: The prediction to evaluate.
        :param Any context: Additional context.
        :return bool: True if the prediction confidence is within thresholds, False otherwise.
        """

        match prediction.flag:
            case PriceDirection.BEARISH:
                lower = self.assignment.bearish_lower_conf
                upper = self.assignment.bearish_upper_conf
                conf = prediction.confidence
                result = lower < conf < upper
                logger.debug(
                    f"[{self.assignment.ticker}] Confidence Check (BEARISH): {lower} < {conf} < {upper} -> {result}"
                )
                return result
            case PriceDirection.BULLISH:
                lower = self.assignment.bullish_lower_conf
                upper = self.assignment.bullish_upper_conf
                conf = prediction.confidence
                result = lower < conf < upper
                logger.debug(
                    f"[{self.assignment.ticker}] Confidence Check (BULLISH): {lower} < {conf} < {upper} -> {result}"
                )
                return result
            case _:
                logger.warning(f"[{self.assignment.ticker}] Unknown prediction flag: {prediction.flag}")
                return False


class PositionSizePredicate(BaseBooleanPredicate[Position]):
    """
    Predicate that checks if the position size is within defined thresholds.
    """

    def apply(self, position: Position = None, **context: Any) -> bool:
        """
        Apply the position size predicate to a position.

        :param Position position: The position to evaluate.
        :param Any context: Additional context.
        :return bool: True if the position size is within thresholds, False otherwise.
        """
        # Use TWS position (true_share_count) instead of internal position (size) for consistency
        current_tws_position = abs(position.true_share_count)
        return current_tws_position < self.assignment.max_position_size


ENTRY_PREDICATES = [
    ConfidenceThresholdPredicate,
    PositionSizePredicate,
]
SCALING_PREDICATES = []

ALL_PREDICATES = [
    *ENTRY_PREDICATES,
    *SCALING_PREDICATES,
]
