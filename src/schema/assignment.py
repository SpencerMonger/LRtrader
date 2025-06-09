"""
The assignment schema is used to describe the full set of parameters assigned to the Trader.
"""

from pathlib import Path
from typing import Dict, List

from loguru import logger
from pydantic import BaseModel, Field, computed_field
import yaml


# The client ID's map to the ASCII values of the ticker symbols.
CLIENT_ID_MAPPING = {
    "AAPL": 65658076,  # ASCII: A(65) + A(65) + P(80) + L(76)
    "AMZN": 65779078,  # ASCII: A(65) + M(77) + Z(90) + N(78)
    "AMD": 657768,  # ASCII: A(65) + M(77) + D(68)
    "GOOGL": 71797971,  # ASCII: G(71) + O(79) + O(79) + G(71) + L(76)
    "TSLA": 84837665,  # ASCII: T(84) + S(83) + L(76) + A(65)
    "NVDA": 78866865,  # ASCII: N(78) + V(86) + D(68) + A(65)
    "AVGO": 89657483,
    # Development TICKERS
    "META": 77696584,  # ASCII: M(77) + E(69) + T(84) + A(65)
    "MSFT": 77838470,  # ASCII: M(77) + S(83) + F(70) + T(84)
    "PLTR": 80887682,  # ASCII: P(80) + L(76) + T(84) + R(82)
    "GME": 111,
    "SHOP": 112,
    "EBAY": 113,
    "BAC": 114,
    "GOOG": 115,
}


class PositionConfig(BaseModel):
    """Configuration for position-wide settings."""

    max_loss_per_trade: float
    max_loss_cumulative: float
    clip_activation: float
    clip_stop_loss: float


class TraderAssignment(BaseModel):
    """
    The TraderAssignment contains the input parameters passed to a single MongerTrader instance.
    """

    ticker: str = Field(...)

    bearish_lower_conf: float = Field(0.85)
    bearish_upper_conf: float = Field(1.0)
    bullish_lower_conf: float = Field(0.65)
    bullish_upper_conf: float = Field(0.75)

    position_size: int = Field(100)
    max_position_size: int = Field(1000)

    trade_threshold: float = Field(
        60,
        description=(
            "The number of seconds to elapse before subsequent executions are considered to be "
            "separate trades."
        ),
    )
    hold_threshold: float = Field(
        300, description="The number of seconds to elapse before a trade must be exited."
    )
    take_profit_target: float = Field(
        0.30, description="The size of the target (in USD) for setting the take profit."
    )
    stop_loss_target: float
    stop_loss_strat: str
    spread_strategy: str
    spread_offset: float

    # Position-wide configuration
    max_loss_per_trade: float
    max_loss_cumulative: float
    clip_activation: float
    clip_stop_loss: float

    inverted: str = Field("regular", description="Signal inversion status: 'regular' or 'inverted'")

    @computed_field
    def client_id(self) -> int:
        """
        The client ID for the connection to TWS.

        The client IDs are set according to a predefined mapping. We are then able
        to easily identify the client ID for a given ticker and ensure uniqueness
        between all clients.

        :return int: The client ID corresponding to the ticker symbol.
        """
        try:
            return CLIENT_ID_MAPPING[self.ticker]
        except KeyError:
            raise ValueError(f"Invalid ticker {self.ticker}")


class AssignmentFactory:
    """Factory for creating TraderAssignment instances from config files."""

    CONFIG_FIELD_MAPPING = {
        # Map config fields to TraderAssignment fields
        "bearish_lower_bound": "bearish_lower_conf",
        "bearish_upper_bound": "bearish_upper_conf",
        "bullish_lower_bound": "bullish_lower_conf",
        "bullish_upper_bound": "bullish_upper_conf",
        "unit_position_size": "position_size",
        "max_hold_time": "hold_threshold",
    }

    @staticmethod
    def load_config(config_path: str | Path) -> Dict:
        """Load configuration from a YAML file."""
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    @classmethod
    def _convert_config_fields(cls, config: Dict) -> Dict:
        """Convert config field names to match TraderAssignment fields."""
        converted = config.copy()
        for config_field, model_field in cls.CONFIG_FIELD_MAPPING.items():
            if config_field in converted:
                converted[model_field] = converted.pop(config_field)
        return converted

    @classmethod
    def create_assignments(cls, config_path: str | Path) -> List[TraderAssignment]:
        """Create TraderAssignment instances from a config file."""
        config = cls.load_config(config_path)

        # Extract position-wide settings
        position_config = config.get("position", {})

        assignments = []
        for ticker, execution_config in config.get("executions", {}).items():
            # Convert config field names to match TraderAssignment fields
            converted_config = cls._convert_config_fields(execution_config)

            # Merge position config with execution config
            full_config = {**converted_config, **position_config}

            # Log the config before creating the assignment object
            logger.debug(f"Creating assignment for {ticker} with config: {full_config}")

            try:
                assignment = TraderAssignment(**full_config)
                assignments.append(assignment)
            except Exception as e:
                logger.error(f"Error creating assignment for {ticker}: {str(e)}")
                continue

        return assignments


if __name__ == "__main__":
    # Test the assignment factory
    config_path = "config.yaml"  # Adjust path as needed

    try:
        assignments = AssignmentFactory.create_assignments(config_path)

        for assignment in assignments:
            print(f"\nAssignment for {assignment.ticker}:")
            print(f"Position Size: {assignment.position_size}")
            print(f"Max Position Size: {assignment.max_position_size}")
            print(f"Take Profit Target: {assignment.take_profit_target}")
            print(f"Stop Loss Target: {assignment.stop_loss_target}")
            print(f"Max Loss Per Trade: {assignment.max_loss_per_trade}")
            print(f"Max Loss Cumulative: {assignment.max_loss_cumulative}")
            print(f"Clip Activation Level: {assignment.clip_activation}")
            print(f"Clip Stop Loss Level: {assignment.clip_stop_loss}")

    except Exception as e:
        print(f"Error loading config: {str(e)}")
