"""
The assignment schema is used to describe the full set of parameters assigned to the Trader.
"""

from pathlib import Path
from typing import Dict, List, Optional

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


class SignalConfig(BaseModel):
    """Configuration for signal providers."""
    
    enable_predictions: bool = Field(True, description="Enable ClickHouse prediction signals")
    enable_news_alerts: bool = Field(False, description="Enable news alert signals")
    news_alert_lookback_minutes: int = Field(3, description="Minutes to look back for news alerts")
    enable_dynamic_discovery: bool = Field(False, description="Enable dynamic ticker discovery from news alerts")
    staggered_order_delay: float = Field(5.0, description="Seconds to wait between entry orders for same ticker")


class DynamicSettings(BaseModel):
    """Configuration for dynamic ticker management."""
    
    max_concurrent_tickers: int = Field(50, description="Maximum number of concurrent tickers to trade")
    ticker_timeout_minutes: int = Field(60, description="Remove inactive tickers after this time")
    enable_contract_validation: bool = Field(True, description="Validate contracts via IBKR API before trading")
    client_id_range_start: int = Field(10000000, description="Starting range for dynamic client IDs")


class TierListEntry(BaseModel):
    """Individual tier entry for price-based position sizing."""
    
    price_min: float = Field(description="Minimum price for this tier (inclusive)")
    price_max: float = Field(description="Maximum price for this tier (exclusive)")
    unit_position_size: int = Field(description="Position size for this price tier")
    max_position_size: int = Field(description="Maximum position size for this price tier")

    def contains_price(self, price: float) -> bool:
        """Check if a price falls within this tier."""
        return self.price_min <= price < self.price_max


class TierList(BaseModel):
    """Price-based position sizing configuration."""
    
    tiers: List[TierListEntry] = Field(default_factory=list, description="List of price tiers")
    
    def get_position_size_for_price(self, price: float) -> Dict[str, int]:
        """
        Get position sizes for a given price.
        
        :param price: Stock price
        :return: Dictionary with 'unit_position_size' and 'max_position_size'
        """
        for tier in self.tiers:
            if tier.contains_price(price):
                return {
                    'unit_position_size': tier.unit_position_size,
                    'max_position_size': tier.max_position_size
                }
        
        # Return empty dict if no tier matches (fallback to global defaults)
        return {}


class GlobalDefaults(BaseModel):
    """Global default trading parameters for dynamic tickers."""
    
    bearish_lower_bound: float = Field(1.0)
    bearish_upper_bound: float = Field(1.01)
    bullish_lower_bound: float = Field(0.0)
    bullish_upper_bound: float = Field(1.01)
    unit_position_size: int = Field(250)
    max_position_size: int = Field(750)
    trade_threshold: float = Field(180)
    max_hold_time: float = Field(900)
    take_profit_target: float = Field(0.002)
    stop_loss_target: float = Field(0.005)
    stop_loss_strat: str = Field("STATIC")
    spread_strategy: str = Field("BEST")
    spread_offset: float = Field(0)
    inverted: str = Field("regular")


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
    
    # Optional dynamic client ID for unknown tickers
    dynamic_client_id: Optional[int] = Field(None, description="Custom client ID for dynamic tickers")

    @computed_field
    def client_id(self) -> int:
        """
        The client ID for the connection to TWS.

        The client IDs are set according to a predefined mapping. We are then able
        to easily identify the client ID for a given ticker and ensure uniqueness
        between all clients.

        :return int: The client ID corresponding to the ticker symbol.
        """
        # If dynamic client ID is set, use it
        if self.dynamic_client_id is not None:
            return self.dynamic_client_id
            
        try:
            return CLIENT_ID_MAPPING[self.ticker]
        except KeyError:
            # For unknown tickers, generate a dynamic client ID
            base_hash = abs(hash(self.ticker)) % 1000000
            client_id = 10000000 + base_hash
            logger.warning(f"Generated dynamic client ID {client_id} for unknown ticker {self.ticker}")
            return client_id


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
        
        # Check if this is a global configuration (has global_defaults)
        if "global_defaults" in config:
            logger.info("Detected global configuration format - dynamic ticker mode available")
            # In global mode, we still process any executions if they exist
            # but the system can also create assignments dynamically
            executions = config.get("executions", {})
        else:
            # Traditional mode - must have executions section
            executions = config.get("executions", {})
            if not executions:
                logger.warning("No executions found in traditional config format")
                return assignments

        for ticker, execution_config in executions.items():
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

    @classmethod
    def create_signal_config(cls, config_path: str | Path) -> SignalConfig:
        """Create SignalConfig from a config file."""
        config = cls.load_config(config_path)
        
        # Extract signal configuration with defaults
        signal_config_data = config.get("signal_strategy", {})
        
        # Check if this is a global config with dynamic discovery
        if "global_defaults" in config:
            # Enable dynamic discovery for global configs by default
            signal_config_data.setdefault("enable_dynamic_discovery", True)
            logger.info("Global configuration detected - enabling dynamic discovery by default")
        
        # Default configuration for backward compatibility
        default_signal_config = {
            "enable_predictions": True,
            "enable_news_alerts": False,
            "news_alert_lookback_minutes": 3,
            "enable_dynamic_discovery": False
        }
        
        # Merge with defaults
        final_config = {**default_signal_config, **signal_config_data}
        
        try:
            signal_config = SignalConfig(**final_config)
            logger.info(f"Created signal config: predictions={signal_config.enable_predictions}, "
                       f"news_alerts={signal_config.enable_news_alerts}, "
                       f"dynamic_discovery={signal_config.enable_dynamic_discovery}, "
                       f"lookback={signal_config.news_alert_lookback_minutes}min")
            return signal_config
        except Exception as e:
            logger.error(f"Error creating signal config: {str(e)}")
            # Return default config on error
            return SignalConfig()

    @classmethod
    def create_dynamic_assignment(cls, ticker: str, config_path: str | Path, client_id: int = None, price: float = None) -> TraderAssignment:
        """Create a TraderAssignment for a dynamically discovered ticker using global defaults."""
        config = cls.load_config(config_path)
        
        # Check if this is a global configuration
        if "global_defaults" not in config:
            raise ValueError("Cannot create dynamic assignment - config file does not have global_defaults section")
        
        # Start with global defaults
        global_defaults = config["global_defaults"]
        position_config = config.get("position", {})
        
        # Apply ticker-specific overrides if they exist
        ticker_overrides = config.get("ticker_overrides", {}).get(ticker, {})
        
        # If ticker has specific overrides, use them as-is (they take precedence over tier list)
        if ticker_overrides:
            logger.info(f"Using ticker-specific overrides for {ticker} (ignoring tier list)")
            assignment_config = {**global_defaults, **position_config, **ticker_overrides}
        else:
            # Use tier-based position sizing if price is available and tier list exists
            tier_based_config = cls._get_tier_based_position_sizing(config, price)
            if tier_based_config:
                logger.info(f"Using tier-based position sizing for {ticker} at ${price:.2f}")
                assignment_config = {**global_defaults, **position_config, **tier_based_config}
            else:
                # Fall back to global defaults
                logger.info(f"Using global defaults for {ticker} (no tier list or price unavailable)")
                assignment_config = {**global_defaults, **position_config}
        
        # Add the ticker
        assignment_config["ticker"] = ticker
        
        # Set dynamic client ID if provided
        if client_id is not None:
            assignment_config["dynamic_client_id"] = client_id
        
        # Convert config field names to match TraderAssignment fields
        converted_config = cls._convert_config_fields(assignment_config)
        
        logger.info(f"Creating dynamic assignment for {ticker} with position_size={converted_config.get('position_size', 'N/A')}")
        logger.debug(f"Dynamic assignment config for {ticker}: {converted_config}")
        
        try:
            return TraderAssignment(**converted_config)
        except Exception as e:
            logger.error(f"Error creating dynamic assignment for {ticker}: {str(e)}")
            raise

    @classmethod
    def _get_tier_based_position_sizing(cls, config: Dict, price: float = None) -> Dict:
        """
        Get position sizing configuration based on price tier list.
        
        :param config: Full configuration dictionary
        :param price: Stock price for tier matching
        :return: Dictionary with tier-based position sizing or empty dict if not applicable
        """
        if price is None:
            logger.debug("Price not available for tier-based position sizing")
            return {}
        
        tier_list_config = config.get("tier_list", [])
        if not tier_list_config:
            logger.debug("No tier list configuration found")
            return {}
        
        try:
            # Create TierList object from configuration
            tier_entries = []
            for tier_config in tier_list_config:
                tier_entry = TierListEntry(**tier_config)
                tier_entries.append(tier_entry)
            
            tier_list = TierList(tiers=tier_entries)
            
            # Get position sizing for the given price
            tier_sizing = tier_list.get_position_size_for_price(price)
            
            if tier_sizing:
                logger.debug(f"Found tier-based sizing for price ${price:.2f}: {tier_sizing}")
                return tier_sizing
            else:
                logger.debug(f"No tier match found for price ${price:.2f}")
                return {}
                
        except Exception as e:
            logger.error(f"Error processing tier list configuration: {e}")
            return {}


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
