# Monger Trading System

## Prerequisites

1. **Interactive Brokers Account**

   - A valid IBKR account (either paper trading or live)
   - Additional usernames can be created through Account Management if you need
     multiple connections

2. **TWS or IB Gateway**

   - Install either Trader Workstation (TWS) or IB Gateway
   - TWS provides a full GUI and trading tools
   - IB Gateway uses ~40% fewer resources but only provides API connectivity
   - Both applications require daily restarts to maintain fresh contract data
   - Version 952 or higher is required for API version 9.72+

3. **API Configuration**
   - Enable API connections in TWS/Gateway:
     1. File → Global Configuration → API → Settings
     2. Enable "Enable ActiveX and Socket Clients"
     3. Add trusted IP addresses if needed
     4. Set appropriate socket port (default: 7496 for live, 7497 for paper)
   - Configure API Precautions:
     1. File → Global Configuration → API → Precautions
     2. Check "Bypass Order Precautions for API Orders" to allow automated
        trading

## Installation

1. **Clone the Repository**

   ```bash
   git clone https://github.com/your-org/monger.git
   cd monger
   ```

2. **Install PDM**

   ```bash
   # Install PDM if you haven't already
   pip install --user pdm

   # Initialize PDM environment (uses Python 3.12)
   pdm install
   ```

3. **Install IBKR's Python API**

   ```bash
   # Create the lib directory if it doesn't exist
   mkdir -p lib
   cd lib

   # Download and extract the IBKR API
   wget http://interactivebrokers.github.io/downloads/twsapi_macunix.1019.01.zip
   unzip twsapi_macunix.1019.01.zip
   

   # The IBJts directory should now be in lib/
   # PDM will automatically find and install it from the path specified in pyproject.toml
   cd ..

   # Verify installation
   pdm run python -c "import ibapi"
   ```

   Note: For Windows users, download the appropriate TWS API package from the
   [IBKR website](https://interactivebrokers.github.io/downloads/twsapi_macunix.1019.01.zip)
   and extract it to the `lib` directory manually.

## Configuration

1. **Create Your Config File**

   ```yaml
   # config.yaml
   executions:
     TICKER1:
       ticker: TICKER1
       unit_position_size: 100
       max_position_size: 300
       # ... other parameters
   ```

2. **Environment Setup**

   ```bash
   # Required for Python imports to work
   export PYTHONPATH=src
   ```

## Running the System

### Local Development (Paper Trading)

```bash
# Start TWS/Gateway in paper trading mode first
python src/run_local.py --config config.yaml --port 7497
```

### Production Environment

```bash
# Start TWS/Gateway in live trading mode first
python src/run_prod.py --config config.yaml --port 7496
```

The system will:

1. Connect to TWS/Gateway using the specified port
2. Create trading threads for each configured ticker
3. Begin monitoring market data and executing trades
4. Handle manual orders via client ID 0

## Monitoring and Debugging

### Logging

- The system uses Loguru for structured logging
- TWS/Gateway maintains separate logs for API events
- Check both log sources when troubleshooting
- Set LOG_LEVEL environment variable to control verbosity

### Paper Trading

- Use paper trading (port 7497) for testing
- Paper trading requires separate market data subscriptions
- All API functionality works identically to live trading

### Common Issues

1. **Connection Problems**

   - Ensure TWS/Gateway is running and API is enabled
   - Verify correct port numbers (7496 live, 7497 paper)
   - Check if another application is using client ID 0

2. **Order Transmission**

   - If orders appear but don't transmit (show 'T' button in TWS):
     - Check API precautions settings
     - Verify market data subscriptions
     - Ensure order parameters are valid

3. **Market Data**

   - Red connection indicators are normal until first data request
   - Verify market data subscriptions for paper trading
   - Check for "pacing violation" messages in logs

4. **Resource Usage**
   - Monitor system resources, especially with multiple tickers
   - Consider IB Gateway for lower resource usage
   - Be aware of TWS/Gateway's daily restart requirement

## Safety Features

1. **Emergency Exits**

   - CTRL+C triggers graceful shutdown
   - All positions attempt orderly closure
   - Orders are cancelled in sequence

2. **Position Monitoring**
   - Continuous position reconciliation
   - Automatic correction of mismatches
   - Manual order integration via client ID 0

## Additional Resources

- [TWS API Documentation](https://interactivebrokers.github.io/tws-api/)
- [IBKR Knowledge Base](https://ibkr.info/article/2484)
- [API Support](https://www.interactivebrokers.com/en/support/solutions.php)

## Limitations

- TWS/Gateway requires GUI for login (no headless operation)
- Daily restart requirement for TWS/Gateway
- Market data subscription fees may apply
- Message rate limits apply to API requests
# newstrader
