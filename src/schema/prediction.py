from enum import Enum

from pydantic import BaseModel, Field


class PriceDirection(str, Enum):
    """
    An enum for the price direction.

    :param BULLISH: The price is expected to increase.
    :param BEARISH: The price is expected to decrease.
    """

    BULLISH = "BULLISH"
    BEARISH = "BEARISH"

    def __str__(self) -> str:
        return str.__str__(self)


class Prediction(BaseModel):
    """
    A prediction from the inference server.

    :param PriceDirection flag: The predicted price direction.
    :param float confidence: The confidence of the prediction.
    """

    flag: PriceDirection = Field(..., description="The predicted price direction.")
    confidence: float = Field(..., description="The confidence of the prediction.")

    @classmethod
    def with_inversion(cls, **data) -> "Prediction":
        """
        Create a prediction with the flag inverted.

        :param dict data: The data to create the prediction.
        :return Prediction: A new Prediction instance with the flag inverted.
        """

        raw_flag = data.get("flag")
        inverted_flag = (
            PriceDirection.BULLISH if raw_flag == PriceDirection.BEARISH else PriceDirection.BEARISH
        )

        return cls(flag=inverted_flag, confidence=data.get("confidence"))
