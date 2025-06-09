import threading
from typing import Optional, Dict, List, Callable
from datetime import datetime

from loguru import logger

# Import the correct PriceDirection enum from the schema
import sys
import os
# Add project root to path to allow importing from src
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)
# Import PriceDirection from the correct location
from src.schema.prediction import PriceDirection

from predictions.prediction_signals import ClickhouseSignalProvider
from predictions.news_alert_signals import NewsAlertSignalProvider


class CompositeSignalProvider:
    """
    A composite signal provider that manages multiple signal providers and 
    presents a unified interface to the trading system.
    
    This provider can combine signals from different sources (predictions, news alerts, etc.)
    and handle priority/conflict resolution between different signal types.
    """

    def __init__(self, ticker_configs: list[dict], signal_config: dict = None):
        """
        Initialize the composite signal provider.
        
        :param ticker_configs: List of ticker configurations
        :param signal_config: Configuration for signal providers
        """
        self._ticker_configs = {config['ticker']: config for config in ticker_configs}
        self._tickers = [config['ticker'] for config in ticker_configs]
        self._signal_config = signal_config or {}
        
        # Initialize sub-providers based on configuration
        self._providers = {}
        self._lock = threading.Lock()
        
        # Combined signals from all providers
        self._latest_signals: Dict[str, dict] = {}
        
        # Signal priority configuration (higher number = higher priority)
        self._signal_priorities = {
            'prediction': 1,
            'news_alert': 2,  # News alerts take priority over predictions
        }
        
        # Initialize configured providers
        self._initialize_providers()
        
        # Callback for new ticker discovery
        self._new_ticker_callback: Optional[Callable[[str], None]] = None

    def _initialize_providers(self):
        """Initialize signal providers based on configuration."""
        # Always initialize prediction provider for backward compatibility
        if self._signal_config.get('enable_predictions', True):
            try:
                self._providers['prediction'] = ClickhouseSignalProvider(ticker_configs=list(self._ticker_configs.values()))
                logger.info("Initialized ClickhouseSignalProvider for predictions")
            except Exception as e:
                logger.error(f"Failed to initialize ClickhouseSignalProvider: {e}")
        
        # Initialize news alert provider if configured
        if self._signal_config.get('enable_news_alerts', False):
            try:
                lookback_minutes = self._signal_config.get('news_alert_lookback_minutes', 3)
                enable_dynamic_discovery = self._signal_config.get('enable_dynamic_discovery', False)
                
                self._providers['news_alert'] = NewsAlertSignalProvider(
                    ticker_configs=list(self._ticker_configs.values()),
                    lookback_minutes=lookback_minutes,
                    enable_dynamic_discovery=enable_dynamic_discovery
                )
                
                mode = "DYNAMIC" if enable_dynamic_discovery else "STATIC"
                logger.info(f"Initialized NewsAlertSignalProvider in {mode} mode with {lookback_minutes} minute lookback")
            except Exception as e:
                logger.error(f"Failed to initialize NewsAlertSignalProvider: {e}")

    def start_polling(self):
        """Start polling for all configured providers."""
        for provider_name, provider in self._providers.items():
            try:
                provider.start_polling()
                logger.info(f"Started polling for {provider_name} provider")
            except Exception as e:
                logger.error(f"Failed to start polling for {provider_name} provider: {e}")

    def stop_polling(self):
        """Stop polling for all configured providers."""
        for provider_name, provider in self._providers.items():
            try:
                provider.stop_polling()
                logger.info(f"Stopped polling for {provider_name} provider")
            except Exception as e:
                logger.error(f"Failed to stop polling for {provider_name} provider: {e}")

    def get_latest_signal(self, ticker: str) -> Optional[dict]:
        """
        Get the latest signal for a ticker, combining signals from all providers
        with priority-based conflict resolution.
        
        :param ticker: Ticker symbol
        :return: Signal dict or None if no signal available
        """
        signals_by_priority = []
        
        # Collect signals from all providers
        for provider_name, provider in self._providers.items():
            try:
                signal = provider.get_latest_signal(ticker)
                if signal:
                    priority = self._signal_priorities.get(provider_name, 0)
                    signals_by_priority.append((priority, provider_name, signal))
            except Exception as e:
                logger.error(f"Error getting signal from {provider_name} for {ticker}: {e}")
        
        if not signals_by_priority:
            return None
        
        # Sort by priority (descending) and return the highest priority signal
        signals_by_priority.sort(key=lambda x: x[0], reverse=True)
        highest_priority_signal = signals_by_priority[0]
        
        provider_name = highest_priority_signal[1]
        signal = highest_priority_signal[2]
        
        # Add metadata about signal source
        enhanced_signal = signal.copy()
        enhanced_signal['source'] = provider_name
        
        with self._lock:
            # Cache the combined signal
            self._latest_signals[ticker] = enhanced_signal
        
        logger.debug(f"Returning {provider_name} signal for {ticker}: {signal['flag']}")
        return enhanced_signal

    def set_new_ticker_callback(self, callback: Callable[[str], None]) -> None:
        """
        Set callback for when new tickers are discovered by any provider.
        
        :param callback: Function to call when a new ticker is found
        """
        self._new_ticker_callback = callback
        
        # Propagate callback to all providers that support it
        for provider_name, provider in self._providers.items():
            if hasattr(provider, 'set_new_ticker_callback'):
                provider.set_new_ticker_callback(callback)
                logger.info(f"Set new ticker callback for {provider_name} provider")

    def get_all_active_tickers(self) -> List[str]:
        """
        Get all tickers that currently have active signals from any provider.
        
        :return: List of ticker symbols with active signals
        """
        active_tickers = set()
        
        for provider_name, provider in self._providers.items():
            try:
                if hasattr(provider, 'get_all_active_tickers'):
                    tickers = provider.get_all_active_tickers()
                    active_tickers.update(tickers)
            except Exception as e:
                logger.error(f"Error getting active tickers from {provider_name}: {e}")
        
        return list(active_tickers)

    def add_ticker_to_watch_list(self, ticker: str) -> None:
        """
        Add a ticker to the monitoring list for all providers.
        
        :param ticker: Ticker symbol to add
        """
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            
        # Add to all providers that support it
        for provider_name, provider in self._providers.items():
            try:
                if hasattr(provider, 'add_ticker_to_watch_list'):
                    provider.add_ticker_to_watch_list(ticker)
                    logger.info(f"Added {ticker} to {provider_name} watch list")
            except Exception as e:
                logger.error(f"Error adding {ticker} to {provider_name} watch list: {e}")

    def get_provider_status(self) -> Dict[str, dict]:
        """
        Get status information for all providers.
        
        :return: Dictionary with provider status information
        """
        status = {}
        
        for provider_name, provider in self._providers.items():
            try:
                # Basic status information
                provider_status = {
                    'initialized': True,
                    'active_tickers': len(provider.get_all_active_tickers()) if hasattr(provider, 'get_all_active_tickers') else 0,
                    'type': type(provider).__name__
                }
                
                # Add provider-specific status if available
                if hasattr(provider, '_polling_thread') and provider._polling_thread:
                    provider_status['polling_active'] = provider._polling_thread.is_alive()
                
                status[provider_name] = provider_status
                
            except Exception as e:
                status[provider_name] = {
                    'initialized': False,
                    'error': str(e),
                    'type': type(provider).__name__
                }
        
        return status

    def clear_old_signals(self, max_age_minutes: int = 30):
        """
        Clear old signals from all providers to prevent memory buildup.
        
        :param max_age_minutes: Maximum age of signals to keep
        """
        for provider_name, provider in self._providers.items():
            try:
                if hasattr(provider, 'clear_old_signals'):
                    provider.clear_old_signals(max_age_minutes)
                    logger.debug(f"Cleared old signals from {provider_name} provider")
            except Exception as e:
                logger.error(f"Error clearing old signals from {provider_name}: {e}")

    def get_signal_summary(self) -> Dict[str, dict]:
        """
        Get a summary of signals from all providers for monitoring purposes.
        
        :return: Dictionary with signal summary information
        """
        summary = {}
        
        for ticker in self._tickers:
            ticker_signals = {}
            
            for provider_name, provider in self._providers.items():
                try:
                    signal = provider.get_latest_signal(ticker)
                    if signal:
                        ticker_signals[provider_name] = {
                            'flag': signal['flag'].name,
                            'timestamp': signal['timestamp'].isoformat(),
                        }
                except Exception as e:
                    ticker_signals[provider_name] = {'error': str(e)}
            
            if ticker_signals:
                summary[ticker] = ticker_signals
        
        return summary


# Factory function for backward compatibility
def create_signal_provider(ticker_configs: list[dict], signal_config: dict = None) -> CompositeSignalProvider:
    """
    Factory function to create a signal provider based on configuration.
    
    :param ticker_configs: List of ticker configurations
    :param signal_config: Signal provider configuration
    :return: Configured signal provider
    """
    # Default signal configuration for backward compatibility
    if signal_config is None:
        signal_config = {
            'enable_predictions': True,
            'enable_news_alerts': False,
            'news_alert_lookback_minutes': 3
        }
    
    return CompositeSignalProvider(ticker_configs, signal_config) 