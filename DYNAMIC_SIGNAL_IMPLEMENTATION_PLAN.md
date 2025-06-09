# Dynamic Signal Prediction Implementation Plan

## Overview

This document outlines the implementation plan for adding a new dynamic signal prediction method to the monger trading system. The solution enables trading on 900+ tickers that change daily, using on-demand contract resolution and global configuration templates.

## Current System Analysis

### Existing Architecture
- **Static Configuration**: Predefined tickers in YAML config with hardcoded `CLIENT_ID_MAPPING`
- **Signal Integration**: `ClickhouseSignalProvider` already integrated and functional
- **Contract Management**: Static contract creation in `TradeMonger.__init__()`
- **Client ID Management**: ASCII-based mapping for predefined tickers

### Limitations
- Hardcoded ticker support (limited to ~25 tickers in `CLIENT_ID_MAPPING`)
- Cannot handle dynamic ticker lists that change daily
- No global configuration template for consistent trading parameters
- Resource waste from pre-loading all potential tickers

## Solution Architecture: Option A - Dynamic Contract Resolution

### Core Principles
1. **On-Demand Trading**: Only create traders when signals appear
2. **Dynamic Contract Resolution**: Resolve contract details using IBKR API when needed
3. **Global Configuration**: Single template applied to all dynamic tickers
4. **Resource Efficiency**: Minimal startup overhead, scalable to 900+ tickers
5. **Backward Compatibility**: Existing static configs continue to work

## Implementation Plan

### Phase 1: Core Infrastructure Changes

#### 1.1 Dynamic Client ID Generation

**File**: `src/schema/assignment.py`

**Changes**:
- Extend `CLIENT_ID_MAPPING` logic for unknown tickers
- Implement hash-based ID generation with collision detection
- Ensure unique client IDs across all traders

**New Components**:
```python
class DynamicClientIdManager:
    """Manages client ID allocation for dynamic tickers"""
    
    @staticmethod
    def generate_client_id(ticker: str) -> int:
        """Generate unique client ID for any ticker"""
        
    @staticmethod
    def is_client_id_available(client_id: int) -> bool:
        """Check if client ID is not in use"""
```

#### 1.2 Global Configuration Handler

**File**: `src/schema/assignment.py`

**Changes**:
- Create `GlobalAssignmentFactory` class
- Support global defaults with ticker-specific overrides
- Maintain backward compatibility with existing `AssignmentFactory`

**New Global Config Format**:
```yaml
signal_strategy:
  name: "clickhouse_dynamic"
  
global_defaults:
  unit_position_size: 250
  max_position_size: 750
  trade_threshold: 180
  max_hold_time: 900
  take_profit_target: 0.002
  stop_loss_target: 0.005
  stop_loss_strat: STATIC
  spread_strategy: BEST
  spread_offset: 0
  inverted: regular

position:
  max_loss_per_trade: 7600
  max_loss_cumulative: 7600
  clip_activation: 8000
  clip_stop_loss: 2000

ticker_overrides:
  TSLA:
    unit_position_size: 100
    max_position_size: 300

model_name: lisa_v0.1
```

#### 1.3 Dynamic Contract Resolution Service

**File**: `src/services/contract_resolver.py` (new)

**Purpose**: Handle IBKR contract resolution for dynamic tickers

**Key Features**:
- Async contract resolution using `reqContractDetails()`
- Contract detail caching to avoid repeated API calls
- Error handling for invalid/delisted tickers
- Rate limiting to respect IBKR API limits

**Interface**:
```python
class ContractResolver:
    """Resolves IBKR contracts for dynamic tickers"""
    
    async def resolve_contract(self, ticker: str) -> Contract:
        """Resolve contract details for ticker"""
        
    def get_cached_contract(self, ticker: str) -> Optional[Contract]:
        """Get cached contract if available"""
        
    def invalidate_cache(self, ticker: str) -> None:
        """Remove ticker from cache"""
```

### Phase 2: Dynamic Trader Management

#### 2.1 Dynamic TradeMonger Factory

**File**: `src/factories/trader_factory.py` (new)

**Purpose**: Create TradeMonger instances on-demand for new tickers

**Key Features**:
- Create traders when signals appear for new tickers
- Apply global configuration template
- Handle contract resolution integration
- Manage trader lifecycle (creation/destruction)

**Interface**:
```python
class DynamicTradeMongerFactory:
    """Factory for creating TradeMonger instances on-demand"""
    
    async def create_trader(self, ticker: str, signal_config: dict) -> TradeMonger:
        """Create new trader for ticker"""
        
    def destroy_trader(self, ticker: str) -> None:
        """Clean up trader resources"""
        
    def get_active_tickers(self) -> List[str]:
        """Get list of currently active tickers"""
```

#### 2.2 Enhanced MongerManager

**File**: `src/manager.py`

**Changes**:
- Support both static and dynamic configuration modes
- Integrate with `DynamicTradeMongerFactory`
- Handle trader lifecycle management
- Implement cleanup for inactive tickers

**New Features**:
- Dynamic trader creation when new signals appear
- Trader cleanup after inactivity timeout
- Support for mixed static/dynamic configurations
- Enhanced error handling and recovery

### Phase 3: Signal Provider Enhancement

#### 3.1 Enhanced ClickhouseSignalProvider

**File**: `predictions/prediction_signals.py`

**Changes**:
- Support callback mechanism for new ticker discovery
- Handle dynamic ticker list updates
- Provide ticker discovery events to manager

**New Features**:
```python
class ClickhouseSignalProvider:
    """Enhanced signal provider with dynamic ticker support"""
    
    def set_new_ticker_callback(self, callback: Callable[[str], None]) -> None:
        """Set callback for when new tickers appear"""
        
    def get_all_active_tickers(self) -> List[str]:
        """Get all tickers currently providing signals"""
        
    def add_ticker_to_watch_list(self, ticker: str) -> None:
        """Add ticker to monitoring list"""
```

#### 3.2 Signal-Driven Trader Creation

**Integration Flow**:
1. `ClickhouseSignalProvider` detects new ticker in signal stream
2. Calls `new_ticker_callback` to notify `MongerManager`
3. `MongerManager` uses `DynamicTradeMongerFactory` to create trader
4. `ContractResolver` resolves IBKR contract details
5. New `TradeMonger` instance starts trading

### Phase 4: Configuration and CLI Updates

#### 4.1 Enhanced CLI Interface

**File**: `src/run_local.py`

**Changes**:
- Add `--mode` parameter to support dynamic/static modes
- Support global configuration files
- Maintain backward compatibility

**New Command Structure**:
```bash
# Static mode (current behavior)
pdm run python src/run_local.py --config test-config.yaml --port 7497 --mode static

# Dynamic mode (new functionality)
pdm run python src/run_local.py --config global-signal-config.yaml --port 7497 --mode dynamic

# Mixed mode (static base + dynamic additions)
pdm run python src/run_local.py --config test-config.yaml --global-config global-signal-config.yaml --port 7497 --mode mixed
```

#### 4.2 Configuration Validation

**Purpose**: Ensure configuration files are valid before startup

**Features**:
- Validate global configuration templates
- Check for required fields
- Validate ticker override configurations
- Provide helpful error messages

## Technical Implementation Details

### Client ID Management Strategy

**Current System**: ASCII-based calculation for predefined tickers
```python
CLIENT_ID_MAPPING = {
    "AAPL": 65658076,  # ASCII: A(65) + A(65) + P(80) + L(76)
    # ...
}
```

**New System**: Hash-based generation with collision detection
```python
def generate_dynamic_client_id(ticker: str) -> int:
    """Generate unique client ID for any ticker"""
    base_hash = hash(ticker) % 1000000  # 6-digit range
    client_id = 10000000 + base_hash    # Start from 10M range
    
    # Handle collisions
    while client_id in used_client_ids:
        client_id += 1
    
    return client_id
```

### Contract Resolution Flow

1. **Signal Arrives**: New ticker detected in signal stream
2. **Cache Check**: Check if contract details already cached
3. **API Request**: If not cached, request from IBKR using `reqContractDetails()`
4. **Validation**: Ensure contract is valid for trading
5. **Cache Storage**: Store resolved contract for future use
6. **Trader Creation**: Create TradeMonger with resolved contract

### Error Handling Strategies

#### Contract Resolution Failures
- **Invalid Ticker**: Log error, skip ticker, continue with others
- **API Timeout**: Retry with exponential backoff
- **Rate Limiting**: Queue requests and respect API limits
- **Network Issues**: Implement circuit breaker pattern

#### Trader Creation Failures
- **Client ID Conflicts**: Generate alternative ID
- **Resource Exhaustion**: Implement trader pool limits
- **Configuration Errors**: Validate and provide helpful messages

### Resource Management

#### Memory Optimization
- **Trader Cleanup**: Remove inactive traders after timeout period
- **Contract Cache**: LRU cache with size limits
- **Signal Buffer**: Limit signal history storage

#### Connection Management
- **Client ID Pool**: Manage available client ID range
- **Connection Limits**: Respect IBKR connection limits
- **Graceful Degradation**: Handle connection failures elegantly

## Benefits and Advantages

### Scalability
- **Dynamic Growth**: Handle 900+ tickers without startup overhead
- **Resource Efficiency**: Only active tickers consume resources
- **Memory Management**: Automatic cleanup of inactive traders

### Flexibility
- **Daily Ticker Changes**: Adapt to changing ticker lists automatically
- **Global Configuration**: Consistent trading parameters across all tickers
- **Override Capability**: Customize parameters for specific tickers

### Operational Excellence
- **Backward Compatibility**: Existing configurations continue to work
- **Monitoring**: Enhanced logging and metrics for dynamic operations
- **Error Recovery**: Robust error handling and recovery mechanisms

## Risk Mitigation

### IBKR API Limitations
- **Rate Limiting**: Implement proper request throttling
- **Connection Limits**: Monitor and manage connection usage
- **Error Handling**: Graceful degradation when API unavailable

### Configuration Management
- **Validation**: Comprehensive configuration validation
- **Defaults**: Sensible defaults for all parameters
- **Documentation**: Clear documentation for configuration options

### System Stability
- **Gradual Rollout**: Phase implementation to minimize risk
- **Monitoring**: Enhanced monitoring and alerting
- **Rollback Plan**: Ability to fall back to static configuration

## Testing Strategy

### Unit Tests
- **Contract Resolution**: Test contract resolver with various ticker types
- **Client ID Generation**: Test uniqueness and collision handling
- **Configuration Parsing**: Test global configuration parsing and validation

### Integration Tests
- **End-to-End Signal Flow**: Test complete signal-to-trade flow
- **IBKR Integration**: Test contract resolution with paper trading
- **Error Scenarios**: Test handling of various error conditions

### Performance Tests
- **Scale Testing**: Test with large numbers of dynamic tickers
- **Memory Usage**: Monitor memory usage under load
- **API Rate Limits**: Test behavior under API rate limiting

## Deployment Strategy

### Phase 1: Infrastructure (Week 1-2)
- Implement core infrastructure changes
- Add dynamic client ID generation
- Create global configuration handler
- Implement contract resolver service

### Phase 2: Dynamic Trading (Week 3-4)
- Add dynamic trader factory
- Enhance MongerManager for dynamic operation
- Integrate signal provider enhancements
- Implement trader lifecycle management

### Phase 3: Integration and Testing (Week 5-6)
- CLI interface updates
- Comprehensive testing
- Documentation updates
- Performance optimization

### Phase 4: Production Rollout (Week 7-8)
- Gradual rollout with monitoring
- Performance tuning
- Bug fixes and optimization
- Final documentation and training

## Success Metrics

### Functional Metrics
- **Ticker Coverage**: Successfully trade on 900+ dynamic tickers
- **Signal Response Time**: Create traders within X seconds of signal appearance
- **Error Rate**: < 1% error rate for contract resolution
- **Resource Usage**: Memory usage remains reasonable under full load

### Operational Metrics
- **Uptime**: Maintain system stability during dynamic operations
- **Recovery Time**: Quick recovery from failures
- **Configuration Accuracy**: Zero configuration-related trading errors

## Conclusion

This implementation plan provides a comprehensive approach to adding dynamic signal prediction capabilities to the monger trading system. The solution maintains backward compatibility while adding the flexibility needed to handle 900+ dynamic tickers efficiently.

The phased approach minimizes risk while delivering value incrementally. The focus on robust error handling and resource management ensures the system can operate reliably in production environments.

Key success factors:
- **Incremental Implementation**: Build and test each component separately
- **Comprehensive Testing**: Thorough testing at each phase
- **Monitoring and Observability**: Enhanced logging and metrics
- **Documentation**: Clear documentation for maintenance and troubleshooting 