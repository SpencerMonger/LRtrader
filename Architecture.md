# Newstrader Architecture Guide

## Overview

Newstrader is a sophisticated algorithmic trading system built for Interactive Brokers (IBKR) that executes trades based on multiple signal sources including machine learning predictions and news alerts. The system is designed for high-frequency, multi-ticker trading with robust risk management and position tracking.

## Core Architecture

### High-Level System Design

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Signal        │    │     Trading     │    │   Interactive   │
│   Providers     │───▶│     Manager     │◀──▶│   Brokers API   │
│                 │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  - Predictions  │    │  - TradeMongers │    │  - Market Data  │
│  - News Alerts  │    │  - Positions    │    │  - Order Status │
│  - Dynamic      │    │  - Risk Mgmt    │    │  - Executions   │
│    Discovery    │    │                 │    │                 │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Directory Structure

```
newstrader/
├── src/                          # Core application code
│   ├── app.py                   # TradeMonger - main trading class
│   ├── manager.py               # MongerManager - orchestrates multiple traders
│   ├── portfolio_app.py         # PortfolioManager - account-level operations
│   ├── run_local.py            # Local development runner
│   ├── run_prod.py             # Production runner with scheduling
│   ├── error.py                # Custom exception definitions
│   ├── THREADS.md              # Threading architecture documentation
│   │
│   ├── schema/                 # Data models and schemas
│   │   ├── __init__.py
│   │   ├── _base.py           # Base model definitions
│   │   ├── assignment.py      # Trader configuration models
│   │   ├── enums.py          # System enumerations
│   │   ├── market.py         # Market data models
│   │   ├── order.py          # Order management models
│   │   ├── position.py       # Position tracking models
│   │   ├── prediction.py     # Prediction/signal models
│   │   ├── trade.py          # Trade lifecycle models
│   │   └── portfolio.py      # Portfolio-level models
│   │
│   ├── logic/                 # Business logic implementations
│   │   ├── __init__.py
│   │   ├── order.py          # Order execution logic
│   │   ├── order_queue.py    # Order queuing system
│   │   └── predicate.py      # Trading predicates and conditions
│   │
│   └── clients/              # External API integrations
│       ├── __init__.py
│       ├── ibkr_client.py    # IBKR API client
│       ├── ibkr_wrapper.py   # IBKR API wrapper/callbacks
│       └── inference.py      # Legacy inference client
│
├── predictions/              # Signal generation modules
│   ├── composite_signal_provider.py  # Signal aggregation
│   ├── prediction_signals.py         # ClickHouse ML predictions
│   └── news_alert_signals.py         # News-based signals
│
├── config files/            # Configuration examples
├── logs/                   # Application logs
└── lib/                   # External libraries (IBKR API)
```

## Core Components

### 1. TradeMonger (`src/app.py`)

The heart of the trading system - manages trading for a single ticker.

**Key Responsibilities:**
- Connects to IBKR TWS/Gateway with unique client ID
- Runs multiple async loops:
  - `signal_check_loop()`: Monitors for trading signals
  - `historical_data_loop()`: Requests market data updates
  - `position_monitor_loop()`: Tracks position and P&L
- Handles order execution through `OrderExecutor`
- Manages emergency exits and risk controls

**Threading Model:**
- Each TradeMonger runs in its own thread
- Uses anyio for async task management
- Thread-safe communication with shared components

### 2. MongerManager (`src/manager.py`)

Orchestrates multiple TradeMonger instances and system lifecycle.

**Key Responsibilities:**
- Creates and manages TradeMonger instances for each ticker
- Initializes signal providers and portfolio manager
- Handles dynamic ticker discovery
- Manages graceful startup/shutdown
- Coordinates emergency exits across all traders

**Signal Integration:**
- Initializes `CompositeSignalProvider` with configuration
- Supports multiple signal sources (predictions, news, dynamic discovery)
- Provides callbacks for new ticker discovery

### 3. PortfolioManager (`src/portfolio_app.py`)

Handles account-level operations and manual order integration.

**Key Responsibilities:**
- Connects with client ID 0 to monitor manual orders
- Tracks account-level P&L and risk metrics
- Forwards manual order updates to relevant TradeMongers
- Implements portfolio-wide risk controls
- Provides order cleanup utilities

### 4. Signal System (`predictions/`)

Multi-source signal aggregation with priority-based resolution.

#### CompositeSignalProvider
- Aggregates signals from multiple providers
- Implements priority-based conflict resolution
- Supports dynamic ticker discovery
- Provides unified interface to trading system

#### Signal Providers:
- **ClickhouseSignalProvider**: ML-based predictions from ClickHouse
- **NewsAlertSignalProvider**: News-based trading signals
- **Dynamic Discovery**: Automatically discovers new tickers from news

### 5. Schema System (`src/schema/`)

Pydantic-based data models ensuring type safety and validation.

#### Key Models:

**TraderAssignment** (`assignment.py`):
- Configuration for individual traders
- Position sizing, risk parameters, trading thresholds
- Client ID mapping and dynamic ticker support

**Position** (`position.py`):
- Tracks open positions and associated orders
- Manages trade lifecycle and P&L calculation
- Handles position reconciliation with broker

**MongerOrder** (`order.py`):
- Represents orders in the system
- Converts to IBKR order format
- Manages order state and validation

**Trade** (`trade.py`):
- Groups related executions into logical trades
- Tracks entry/exit timing and P&L
- Manages bracket orders (take profit/stop loss)

### 6. Order Execution System (`src/logic/order.py`)

Sophisticated order management with multiple execution strategies.

#### OrderExecutor Class:
- **Order Types**: ENTRY, TAKE_PROFIT, STOP_LOSS, EXIT, EMERGENCY_EXIT, DANGLING_SHARES
- **Status Handling**: Submitted, filled, cancelled order processing
- **Risk Management**: Position limits, stop loss cooldowns, P&L checks
- **Market Integration**: Real-time price updates and order adjustments

#### Key Features:
- **Bracket Orders**: Automatic take profit and stop loss placement
- **Position Reconciliation**: Handles discrepancies with broker positions
- **Emergency Protocols**: Rapid position liquidation capabilities
- **Predicate System**: Configurable trading conditions and filters

## Data Flow

### 1. Signal Generation Flow
```
Signal Sources → CompositeSignalProvider → TradeMonger → OrderExecutor → IBKR
     │                    │                    │            │           │
     ▼                    ▼                    ▼            ▼           ▼
┌─────────┐    ┌─────────────────┐    ┌─────────────┐  ┌─────────┐  ┌─────────┐
│ClickHouse│    │ Priority-based  │    │ Signal      │  │ Order   │  │ Market  │
│ ML Model │    │ Aggregation     │    │ Processing  │  │ Placing │  │ Execution│
│         │    │                 │    │             │  │         │  │         │
│ News    │    │ Conflict        │    │ Predicate   │  │ Status  │  │ Fills   │
│ Alerts  │    │ Resolution      │    │ Validation  │  │ Updates │  │ Updates │
└─────────┘    └─────────────────┘    └─────────────┘  └─────────┘  └─────────┘
```

### 2. Order Lifecycle Flow
```
Signal → Entry Order → Fill → Bracket Orders → Management → Exit
   │         │          │         │             │          │
   ▼         ▼          ▼         ▼             ▼          ▼
Validate → Place → Monitor → Set TP/SL → Adjust → Close Position
   │         │          │         │             │          │
   ▼         ▼          ▼         ▼             ▼          ▼
Position → Order → Update → Create → Market → Calculate
 Sizing    Queue   Position  Trades   Data     P&L
```

### 3. Risk Management Flow
```
Market Data → Position Monitor → Risk Checks → Action
     │             │               │           │
     ▼             ▼               ▼           ▼
Price Updates → P&L Calculation → Threshold → Emergency Exit
     │             │               │           │
     ▼             ▼               ▼           ▼
Order Book → Unrealized P&L → Stop Loss → Cancel Orders
     │             │               │           │
     ▼             ▼               ▼           ▼
Spreads → Position Size → Cooldowns → Liquidate
```

## Configuration System

### Assignment Configuration
The system uses YAML configuration files to define trading parameters:

```yaml
# Traditional ticker-specific configuration
executions:
  AAPL:
    ticker: AAPL
    unit_position_size: 100
    max_position_size: 300
    take_profit_target: 0.002
    stop_loss_target: 0.005
    # ... other parameters

# Global configuration with dynamic discovery
global_defaults:
  unit_position_size: 250
  max_position_size: 750
  # ... other defaults

signal_config:
  enable_predictions: true
  enable_news_alerts: true
  enable_dynamic_discovery: true
  news_alert_lookback_minutes: 3
```

### Key Configuration Concepts:

**Position Sizing:**
- `unit_position_size`: Base position size for entries
- `max_position_size`: Maximum total position size
- Tier-based sizing based on stock price

**Risk Parameters:**
- `take_profit_target`: Profit target (percentage or absolute)
- `stop_loss_target`: Loss limit (percentage or absolute)
- `max_loss_per_trade`: Per-trade loss limit
- `max_loss_cumulative`: Total loss limit

**Timing Controls:**
- `trade_threshold`: Time between separate trades
- `hold_threshold`: Maximum hold time for positions

## Threading Architecture

### Thread Organization
```
Main Process
├── MongerManager (Main Thread)
│   ├── TradeMonger-AAPL (Thread 1, Client ID: 65658076)
│   ├── TradeMonger-TSLA (Thread 2, Client ID: 84837665)
│   ├── TradeMonger-NVDA (Thread 3, Client ID: 78866865)
│   ├── PortfolioManager (Thread N, Client ID: 0)
│   └── Dynamic Trader Manager (Async Task)
│
├── Signal Providers (Background Threads)
│   ├── ClickHouse Polling Thread
│   └── News Alert Polling Thread
│
└── IBKR API Threads (Per Connection)
    ├── Market Data Reader
    ├── Order Status Reader
    └── Position Update Reader
```

### Thread Communication:
- **Thread-Safe**: All shared data structures use locks
- **Event-Driven**: Signal updates trigger trading actions
- **Queue-Based**: Order execution uses queued processing
- **Async Integration**: anyio for coroutine management

## Order Types and Strategies

### Order Types:
1. **ENTRY**: Market entry orders with 60-second GTD expiration
2. **TAKE_PROFIT**: Profit-taking limit orders
3. **STOP_LOSS**: Stop-limit orders for loss protection
4. **EXIT**: Position closure orders with 10-second GTD
5. **EMERGENCY_EXIT**: Market orders for immediate liquidation
6. **DANGLING_SHARES**: Reconciliation orders for position mismatches

### Trading Strategies:
- **Bracket Orders**: Automatic TP/SL placement after entry fills
- **Position Scaling**: Multiple entries within trade threshold
- **Time-Based Exits**: Automatic position closure after hold threshold
- **Risk-Based Exits**: Stop loss activation and cooldown periods

## Error Handling and Recovery

### Exception Hierarchy:
- `InvalidExecutionError`: Invalid order operations
- `OrderDoesNotExistError`: Missing order references
- `CannotModifyFilledOrderError`: Illegal order modifications
- `TradeLockedError`: Attempts to modify locked trades
- `StopLossCooldownIsActiveError`: Stop loss cooldown violations

### Recovery Mechanisms:
- **Position Reconciliation**: Automatic broker position sync
- **Order Cleanup**: Stale order detection and cancellation
- **Emergency Protocols**: Rapid liquidation capabilities
- **Graceful Shutdown**: Ordered system termination

## Deployment Patterns

### Local Development (`run_local.py`):
- Immediate activation of all traders
- Debug-level logging
- Paper trading support
- Manual control and testing

### Production Deployment (`run_prod.py`):
- Scheduled trading sessions (morning/afternoon)
- State management (warmup/active/cooldown/inactive)
- Automatic session transitions
- Production risk controls

### Environment Requirements:
- Python 3.12
- Interactive Brokers TWS or IB Gateway
- ClickHouse database (for ML predictions)
- News data sources (for alerts)

## Key Design Patterns

### 1. Factory Pattern
- `AssignmentFactory`: Creates trader configurations
- `OrderFactory`: Creates different order types
- Dynamic trader creation for new tickers

### 2. Observer Pattern
- Signal providers notify of new signals
- Order status updates trigger position changes
- Market data updates drive trading decisions

### 3. Strategy Pattern
- Multiple signal providers with different strategies
- Configurable order execution strategies
- Pluggable risk management rules

### 4. Command Pattern
- Queued order execution
- Reversible trading actions
- Batch order operations

## Performance Considerations

### Optimization Strategies:
- **Threading**: Parallel processing of multiple tickers
- **Caching**: Signal and market data caching
- **Batching**: Grouped order operations
- **Connection Pooling**: Efficient IBKR API usage

### Scalability Limits:
- IBKR client ID limits (~100 concurrent connections)
- Memory usage scales with number of active positions
- Network bandwidth for real-time data feeds
- Database query performance for signal generation

## Security and Risk Management

### Risk Controls:
- **Position Limits**: Per-ticker and portfolio-wide limits
- **Loss Limits**: Stop losses and maximum drawdown controls
- **Time Limits**: Maximum position hold times
- **Circuit Breakers**: Emergency exit capabilities

### Security Features:
- **API Key Management**: Secure credential handling
- **Client ID Isolation**: Separate connections per ticker
- **Audit Logging**: Complete trade and order history
- **Configuration Validation**: Type-safe parameter checking

## Monitoring and Observability

### Logging System:
- **Loguru**: Structured logging with multiple levels
- **Per-Ticker Logging**: Separate log streams for each trader
- **Performance Metrics**: Execution timing and success rates
- **Error Tracking**: Exception monitoring and alerting

### Key Metrics:
- **Trading Performance**: P&L, win rate, average trade duration
- **System Performance**: Order execution speed, signal latency
- **Risk Metrics**: Maximum drawdown, position concentration
- **Operational Metrics**: Uptime, error rates, connection status

## Future Architecture Considerations

### Scalability Improvements:
- **Microservices**: Separate signal generation from execution
- **Message Queues**: Decouple components with async messaging
- **Database Sharding**: Distribute data across multiple databases
- **Container Orchestration**: Kubernetes deployment patterns

### Feature Enhancements:
- **Machine Learning Pipeline**: Automated model training and deployment
- **Advanced Risk Models**: Portfolio optimization and correlation analysis
- **Multi-Asset Support**: Options, futures, and crypto trading
- **Real-Time Analytics**: Live performance dashboards

This architecture provides a robust foundation for algorithmic trading with clear separation of concerns, comprehensive risk management, and scalable design patterns. 