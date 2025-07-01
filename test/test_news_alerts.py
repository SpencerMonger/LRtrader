#!/usr/bin/env python3
"""
Test script for the NewsAlertSignalProvider
"""

import sys
import os
import time
from datetime import datetime

# Add project root to path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

from loguru import logger
from predictions.news_alert_signals import NewsAlertSignalProvider
from predictions.composite_signal_provider import CompositeSignalProvider

# Configure logging
log_fmt = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
logger.add("test_news_alerts.log", rotation="10 MB", level="DEBUG", format=log_fmt)


def test_news_alert_provider():
    """Test the NewsAlertSignalProvider directly."""
    logger.info("Testing NewsAlertSignalProvider...")
    
    # Test ticker configurations
    test_ticker_configs = [
        {'ticker': "AAPL", 'inverted': 'regular'},
        {'ticker': "TSLA", 'inverted': 'inverted'},
        {'ticker': "META", 'inverted': 'regular'},
    ]
    
    try:
        # Initialize provider with 3-minute lookback
        provider = NewsAlertSignalProvider(
            ticker_configs=test_ticker_configs,
            lookback_minutes=3
        )
        
        logger.info("NewsAlertSignalProvider initialized successfully")
        
        # Start polling
        provider.start_polling()
        logger.info("Started polling for news alerts")
        
        # Monitor for signals for 60 seconds
        start_time = time.time()
        while time.time() - start_time < 60:
            for config in test_ticker_configs:
                ticker = config['ticker']
                signal = provider.get_latest_signal(ticker)
                
                if signal:
                    logger.info(f"NEWS ALERT SIGNAL - {ticker}: {signal['flag'].name} at {signal['timestamp']}")
                else:
                    logger.debug(f"No signal for {ticker}")
            
            time.sleep(5)  # Check every 5 seconds
        
        # Stop polling
        provider.stop_polling()
        logger.info("Stopped news alert polling")
        
    except Exception as e:
        logger.exception(f"Error testing NewsAlertSignalProvider: {e}")
        return False
    
    return True


def test_composite_provider():
    """Test the CompositeSignalProvider with news alerts enabled."""
    logger.info("Testing CompositeSignalProvider with news alerts...")
    
    # Test ticker configurations
    test_ticker_configs = [
        {'ticker': "AAPL", 'inverted': 'regular'},
        {'ticker': "TSLA", 'inverted': 'regular'},
        {'ticker': "META", 'inverted': 'regular'},
    ]
    
    # Signal configuration with news alerts enabled
    signal_config = {
        'enable_predictions': True,
        'enable_news_alerts': True,
        'news_alert_lookback_minutes': 3
    }
    
    try:
        # Initialize composite provider
        provider = CompositeSignalProvider(
            ticker_configs=test_ticker_configs,
            signal_config=signal_config
        )
        
        logger.info("CompositeSignalProvider initialized successfully")
        
        # Check provider status
        status = provider.get_provider_status()
        logger.info(f"Provider status: {status}")
        
        # Start polling
        provider.start_polling()
        logger.info("Started polling for all signal types")
        
        # Monitor for signals for 60 seconds
        start_time = time.time()
        while time.time() - start_time < 60:
            for config in test_ticker_configs:
                ticker = config['ticker']
                signal = provider.get_latest_signal(ticker)
                
                if signal:
                    source = signal.get('source', 'unknown')
                    logger.info(f"COMPOSITE SIGNAL - {ticker}: {signal['flag'].name} from {source} at {signal['timestamp']}")
                else:
                    logger.debug(f"No signal for {ticker}")
            
            # Get signal summary
            summary = provider.get_signal_summary()
            if summary:
                logger.debug(f"Signal summary: {summary}")
            
            time.sleep(10)  # Check every 10 seconds
        
        # Stop polling
        provider.stop_polling()
        logger.info("Stopped composite signal polling")
        
    except Exception as e:
        logger.exception(f"Error testing CompositeSignalProvider: {e}")
        return False
    
    return True


def test_config_integration():
    """Test loading signal configuration from a config file."""
    logger.info("Testing configuration integration...")
    
    try:
        from src.schema.assignment import AssignmentFactory
        
        # Test with the new config file
        config_path = "test-config-with-news.yaml"
        
        if not os.path.exists(config_path):
            logger.warning(f"Config file {config_path} not found, skipping config integration test")
            return True
        
        # Load signal configuration
        signal_config = AssignmentFactory.create_signal_config(config_path)
        logger.info(f"Loaded signal config: predictions={signal_config.enable_predictions}, "
                   f"news_alerts={signal_config.enable_news_alerts}, "
                   f"lookback={signal_config.news_alert_lookback_minutes}min")
        
        # Load assignments
        assignments = AssignmentFactory.create_assignments(config_path)
        logger.info(f"Loaded {len(assignments)} trader assignments")
        
        # Test creating composite provider with config
        ticker_configs = [
            {"ticker": a.ticker, "inverted": a.inverted}
            for a in assignments
        ]
        
        signal_config_dict = {
            'enable_predictions': signal_config.enable_predictions,
            'enable_news_alerts': signal_config.enable_news_alerts,
            'news_alert_lookback_minutes': signal_config.news_alert_lookback_minutes
        }
        
        provider = CompositeSignalProvider(
            ticker_configs=ticker_configs,
            signal_config=signal_config_dict
        )
        
        logger.info("Configuration integration test successful")
        
        # Quick status check
        status = provider.get_provider_status()
        for provider_name, provider_status in status.items():
            if provider_status.get('initialized', False):
                logger.info(f"Provider {provider_name} initialized successfully")
            else:
                logger.warning(f"Provider {provider_name} failed to initialize: {provider_status.get('error', 'Unknown error')}")
        
    except Exception as e:
        logger.exception(f"Error testing configuration integration: {e}")
        return False
    
    return True


def main():
    """Run all tests."""
    logger.info("Starting NewsAlertSignalProvider tests...")
    
    # Create .env file if it doesn't exist
    env_path = "predictions/.env"
    if not os.path.exists(env_path):
        logger.info("Creating dummy .env file for testing...")
        os.makedirs("predictions", exist_ok=True)
        with open(env_path, "w") as f:
            f.write("CLICKHOUSE_HOST=localhost\n")
            f.write("CLICKHOUSE_HTTP_PORT=8123\n")
            f.write("CLICKHOUSE_USER=default\n")
            f.write("CLICKHOUSE_PASSWORD=\n")
            f.write("CLICKHOUSE_DATABASE=default\n")
            f.write("CLICKHOUSE_SECURE=false\n")
    
    tests = [
        ("News Alert Provider", test_news_alert_provider),
        ("Composite Provider", test_composite_provider),
        ("Configuration Integration", test_config_integration),
    ]
    
    results = []
    for test_name, test_func in tests:
        logger.info(f"\n{'='*50}")
        logger.info(f"Running test: {test_name}")
        logger.info(f"{'='*50}")
        
        try:
            success = test_func()
            results.append((test_name, success))
            status = "PASSED" if success else "FAILED"
            logger.info(f"Test {test_name}: {status}")
        except Exception as e:
            logger.exception(f"Test {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    logger.info(f"\n{'='*50}")
    logger.info("TEST SUMMARY")
    logger.info(f"{'='*50}")
    
    passed = 0
    for test_name, success in results:
        status = "PASSED" if success else "FAILED"
        logger.info(f"{test_name}: {status}")
        if success:
            passed += 1
    
    logger.info(f"\nTotal: {len(results)} tests, {passed} passed, {len(results) - passed} failed")
    
    if passed == len(results):
        logger.info("All tests passed! News alert integration is ready.")
    else:
        logger.warning("Some tests failed. Check logs for details.")


if __name__ == "__main__":
    main() 