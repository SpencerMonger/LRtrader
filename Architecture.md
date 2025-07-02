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
│   │   ├── order_queue.py    # Enhanced order queuing system with staggered delays
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
│   └── news_alert_signals.py         # News-based signals with 1s polling
│
├── config files/            # Configuration examples
├── logs/                   # Application logs
├── test/                   # Test suite including position sizing tests
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
  - `position_monitor_loop()`: Tracks position and P&L with safety checks
- Handles order execution through `OrderExecutor`
- Manages emergency exits and risk controls

**Recent Enhancements:**
- **Position Safety Net**: Added `handle_max_position_size_check()` to position monitor loop
- **Real-time Risk Monitoring**: Position size validation every second
- **Enhanced Logging**: Better visibility into position tracking and order flow

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

### 4. Enhanced Signal System (`predictions/`)

Multi-source signal aggregation with priority-based resolution and optimized timing.

#### CompositeSignalProvider
- Aggregates signals from multiple providers
- Implements priority-based conflict resolution
- Supports dynamic ticker discovery
- Provides unified interface to trading system

#### Signal Providers:
- **ClickhouseSignalProvider**: ML-based predictions from ClickHouse
- **NewsAlertSignalProvider**: News-based trading signals with 1-second polling
- **Dynamic Discovery**: Automatically discovers new tickers from news

#### Recent News Alert Improvements:
- **Faster Polling**: Reduced from 2s to 1s for quicker signal detection
- **Duplicate Prevention**: Tracks processed alerts to prevent duplicate signals
- **Memory Management**: Automatic cleanup of old processed alerts
- **Freshness Validation**: Only processes news within 10-second window

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

### 6. Enhanced Order Execution System (`src/logic/order.py`)

Sophisticated order management with multiple execution strategies and critical position sizing fixes.

#### OrderExecutor Class:
- **Order Types**: ENTRY, TAKE_PROFIT, STOP_LOSS, EXIT, EMERGENCY_EXIT, DANGLING_SHARES
- **Status Handling**: Submitted, filled, cancelled order processing
- **Risk Management**: Position limits, stop loss cooldowns, P&L checks
- **Market Integration**: Real-time price updates and order adjustments

#### Critical Position Sizing Fix:
- **Immediate Fill Validation**: Position size checked instantly when ENTRY orders fill
- **Automatic Order Cancellation**: Pending ENTRY orders cancelled when position >= max_position_size
- **Preserves Exit Orders**: Only cancels ENTRY orders, keeps TAKE_PROFIT/STOP_LOSS orders
- **Comprehensive Logging**: Detailed logging of position checks and order cancellations
- **Method**: `handle_max_position_size_check()` with `@queued_execution` decorator

#### Key Features:
- **Bracket Orders**: Automatic take profit and stop loss placement
- **Position Reconciliation**: Handles discrepancies with broker positions
- **Emergency Protocols**: Rapid position liquidation capabilities
- **Predicate System**: Configurable trading conditions and filters

### 7. Advanced Order Queue System (`src/logic/order_queue.py`)

Enhanced queuing system with intelligent staggered delays and timing optimizations.

#### OrderQueue Class Features:
- **Smart Staggered Delays**: Configurable delays between entry orders for same ticker
- **First Order Optimization**: No delay for the very first order of each ticker
- **Per-Ticker Tracking**: Maintains last order time for each ticker independently
- **Thread-Safe Processing**: Queue-based execution with proper locking
- **Graceful Shutdown**: Clean thread termination with timeout handling

#### Timing Improvements:
- **First Order**: Immediate processing (0s delay)
- **Subsequent Orders**: Configurable stagger delay (default 8s, optimized to 2s in some configs)
- **Intelligent Logic**: `if last_time == 0` skips delay for first order
- **Performance Logging**: Detailed timing information for debugging

#### Key Methods:
- `enqueue()`: Thread-safe order queuing with ticker extraction
- `_worker()`: Background processing with staggered delay logic
- `@queued_execution` decorator: Marks methods for queued processing

## Data Flow

### 1. Enhanced Signal Generation Flow
```
Signal Sources → CompositeSignalProvider → TradeMonger → OrderQueue → OrderExecutor → IBKR
     │                    │                    │            │            │           │
     ▼                    ▼                    ▼            ▼            ▼           ▼
┌─────────┐    ┌─────────────────┐    ┌─────────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
│ClickHouse│    │ Priority-based  │    │ Signal      │  │ Stagger │  │ Order   │  │ Market  │
│ ML Model │    │ Aggregation     │    │ Processing  │  │ Delay   │  │ Placing │  │ Execution│
│ (2s poll)│    │                 │    │             │  │ Logic   │  │         │  │         │
│         │    │ Conflict        │    │ Predicate   │  │ First:  │  │ Status  │  │ Fills   │
│ News    │    │ Resolution      │    │ Validation  │  │ 0s      │  │ Updates │  │ Updates │
│ Alerts  │    │                 │    │             │  │ Next:   │  │         │  │         │
│ (1s poll)│    │                 │    │             │  │ 8s      │  │         │  │         │
│         │    │                 │    │             │  │         │  │         │  │         │
└─────────┘    └─────────────────┘    └─────────────┘  └─────────┘  └─────────┘  └─────────┘
```

### 2. Enhanced Order Lifecycle Flow with Position Checks
```
Signal → OrderQueue → Entry Order → Fill → Position Check → Bracket Orders → Management → Exit
   │         │           │          │         │              │             │          │
   ▼         ▼           ▼          ▼         ▼              ▼             ▼          ▼
Validate → Stagger → Place → Monitor → Size Check → Cancel Pending → Set TP/SL → Close Position
   │      Delay       │          │         │              │             │          │
   ▼         ▼           ▼          ▼         ▼              ▼             ▼          ▼
Position → First: 0s → Order → Update → if >= max → Find ENTRY → Create → Calculate
 Sizing    Next: 8s   Queue   Position  Cancel All   Orders    Trades     P&L
```

### 3. Enhanced Risk Management Flow with Real-Time Checks
```
Market Data → Position Monitor → Risk Checks → Immediate Action
     │             │               │              │
     ▼             ▼               ▼              ▼
Price Updates → P&L Calculation → Position Size → Cancel Orders
     │             │               │              │
     ▼             ▼               ▼              ▼
Order Book → Unrealized P&L → >= Max Size → Emergency Exit
     │             │               │              │
     ▼             ▼               ▼              ▼
Spreads → Position Size → Stop Loss → Liquidate
     │             │               │              │
     ▼             ▼               ▼              ▼
Real-time → Every 1 Second → Cooldowns → Preserve TP/SL
```

## Configuration System

### Assignment Configuration
The system uses YAML configuration files to define trading parameters:

```yaml
# Signal strategy configuration with timing optimizations
signal_strategy:
  enable_predictions: false         # Disable traditional prediction signals
  enable_news_alerts: true          # Enable news alert signals
  news_alert_lookback_minutes: 2    # Minutes to look back for unique ticker detection
  enable_dynamic_discovery: true    # Enable dynamic ticker discovery
  staggered_order_delay: 8.0        # Seconds between entry orders (optimized to 2.0s in some configs)

# Price-based position sizing tier list
tier_list:
  - price_min: 0.01
    price_max: 1.00
    unit_position_size: 10000
    max_position_size: 20000
  - price_min: 1.00
    price_max: 3.00
    unit_position_size: 8000
    max_position_size: 16000
  # ... additional tiers

# Global trading defaults with enhanced risk management
global_defaults:
  unit_position_size: 1000           # Default position size
  max_position_size: 3000            # Maximum position size (strictly enforced)
  take_profit_target: 0.85          # Take profit target
  stop_loss_target: 0.30            # Stop loss target
  # ... other parameters

# Position-wide risk management settings
position:
  max_loss_per_trade: 4000          # Maximum loss per trade
  max_loss_cumulative: 6000         # Maximum cumulative loss
  clip_activation: 8000             # Clip activation level
  clip_stop_loss: 2000             # Clip stop loss level
```

### Key Configuration Concepts:

**Enhanced Position Sizing:**
- `unit_position_size`: Base position size for entries
- `max_position_size`: **Strictly enforced** maximum total position size
- Tier-based sizing based on stock price
- **Real-time validation** on every order fill

**Timing Controls:**
- `staggered_order_delay`: Delay between entry orders (first order skips delay)
- `trade_threshold`: Time between separate trades
- `hold_threshold`: Maximum hold time for positions

**Risk Parameters:**
- `take_profit_target`: Profit target (percentage or absolute)
- `stop_loss_target`: Loss limit (percentage or absolute)
- `max_loss_per_trade`: Per-trade loss limit
- `max_loss_cumulative`: Total loss limit

## Threading Architecture

### Thread Organization
```
Main Process
├── MongerManager (Main Thread)
│   ├── TradeMonger-AAPL (Thread 1, Client ID: 65658076)
│   │   └── OrderQueue Worker Thread (Staggered Delays)
│   ├── TradeMonger-TSLA (Thread 2, Client ID: 84837665)
│   │   └── OrderQueue Worker Thread (Staggered Delays)
│   ├── TradeMonger-NVDA (Thread 3, Client ID: 78866865)
│   │   └── OrderQueue Worker Thread (Staggered Delays)
│   ├── PortfolioManager (Thread N, Client ID: 0)
│   └── Dynamic Trader Manager (Async Task)
│
├── Signal Providers (Background Threads)
│   ├── ClickHouse Polling Thread (Every 2s)
│   └── News Alert Polling Thread (Every 1s - OPTIMIZED)
│
└── IBKR API Threads (Per Connection)
    ├── Market Data Reader
    ├── Order Status Reader
    └── Position Update Reader
```

### Thread Communication:
- **Thread-Safe**: All shared data structures use locks
- **Event-Driven**: Signal updates trigger trading actions
- **Queue-Based**: Order execution uses enhanced queued processing
- **Async Integration**: anyio for coroutine management

## Order Types and Strategies

### Order Types:
1. **ENTRY**: Market entry orders with 60-second GTD expiration
2. **TAKE_PROFIT**: Profit-taking limit orders
3. **STOP_LOSS**: Stop-limit orders for loss protection
4. **EXIT**: Position closure orders with 10-second GTD
5. **EMERGENCY_EXIT**: Market orders for immediate liquidation
6. **DANGLING_SHARES**: Reconciliation orders for position mismatches

### Enhanced Trading Strategies:
- **Smart Order Queuing**: First order immediate, subsequent orders staggered
- **Bracket Orders**: Automatic TP/SL placement after entry fills
- **Position Scaling**: Multiple entries within trade threshold (with size limits)
- **Time-Based Exits**: Automatic position closure after hold threshold
- **Risk-Based Exits**: Stop loss activation and cooldown periods
- **Real-Time Position Monitoring**: Continuous position size validation

## Error Handling and Recovery

### Exception Hierarchy:
- `InvalidExecutionError`: Invalid order operations
- `OrderDoesNotExistError`: Missing order references
- `CannotModifyFilledOrderError`: Illegal order modifications
- `TradeLockedError`: Attempts to modify locked trades
- `StopLossCooldownIsActiveError`: Stop loss cooldown violations

### Enhanced Recovery Mechanisms:
- **Position Reconciliation**: Automatic broker position sync
- **Order Cleanup**: Stale order detection and cancellation
- **Emergency Protocols**: Rapid position liquidation capabilities
- **Graceful Shutdown**: Ordered system termination
- **Position Size Enforcement**: Automatic order cancellation when limits exceeded
- **Real-Time Monitoring**: Continuous system health checks

## Performance Optimizations

### Recent Timing Improvements:
- **News Alert Polling**: Reduced from 2s to 1s (50% improvement)
- **First Order Delay**: Eliminated 8s delay for first orders (100% improvement)
- **Signal Processing**: Optimized freshness validation (10s window)
- **Position Monitoring**: Real-time checks every 1 second

### Signal-to-Order Latency:
- **Previous**: News alert → First order: ~20 seconds
- **Current**: News alert → First order: ~3.75 seconds
- **Improvement**: 85% reduction in latency

### Optimization Strategies:
- **Threading**: Parallel processing of multiple tickers
- **Caching**: Signal and market data caching
- **Batching**: Grouped order operations
- **Connection Pooling**: Efficient IBKR API usage
- **Smart Queuing**: Optimized order processing with intelligent delays

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
- Enhanced queued order execution with timing logic
- Reversible trading actions
- Batch order operations

### 5. Decorator Pattern
- `@queued_execution` decorator for order methods
- Position size validation decorators
- Logging and monitoring decorators

## Security and Risk Management

### Enhanced Risk Controls:
- **Position Limits**: Per-ticker and portfolio-wide limits (strictly enforced)
- **Real-Time Monitoring**: Position size validation every second
- **Automatic Order Cancellation**: Immediate cancellation when limits exceeded
- **Loss Limits**: Stop losses and maximum drawdown controls
- **Time Limits**: Maximum position hold times
- **Circuit Breakers**: Emergency exit capabilities

### Position Sizing Bug Fix:
- **Problem**: System accumulated positions exceeding configured limits
- **Root Cause**: Position checks only occurred during signal processing (every 8s)
- **Solution**: Added immediate position checks when ENTRY orders fill
- **Implementation**: `handle_max_position_size_check()` method with queued execution
- **Result**: Prevents position size violations in real-time

### Security Features:
- **API Key Management**: Secure credential handling
- **Client ID Isolation**: Separate connections per ticker
- **Audit Logging**: Complete trade and order history
- **Configuration Validation**: Type-safe parameter checking

## Monitoring and Observability

### Enhanced Logging System:
- **Loguru**: Structured logging with multiple levels
- **Per-Ticker Logging**: Separate log streams for each trader
- **Performance Metrics**: Execution timing and success rates
- **Error Tracking**: Exception monitoring and alerting
- **Position Monitoring**: Real-time position size logging
- **Order Flow Tracking**: Detailed order lifecycle logging

### Key Metrics:
- **Trading Performance**: P&L, win rate, average trade duration
- **System Performance**: Order execution speed, signal latency
- **Risk Metrics**: Maximum drawdown, position concentration
- **Operational Metrics**: Uptime, error rates, connection status
- **Timing Metrics**: Signal-to-order latency, order processing speed

## Testing and Quality Assurance

### Test Suite (`test/`):
- **Position Sizing Tests**: Comprehensive validation of position limits
- **Timing Improvement Tests**: Verification of staggered delay logic
- **Integration Tests**: End-to-end system testing
- **Unit Tests**: Component-level testing

### Test Coverage:
- Position size validation logic
- Order queue timing behavior
- Signal processing accuracy
- Risk management controls

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
- **Enhanced Position Management**: More sophisticated position sizing algorithms

This architecture provides a robust foundation for algorithmic trading with clear separation of concerns, comprehensive risk management, optimized performance, and scalable design patterns. The recent enhancements significantly improve system reliability, timing performance, and risk control capabilities. 