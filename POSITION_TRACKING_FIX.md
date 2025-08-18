# Position Tracking Race Condition Fix

## Root Cause Analysis: GNS Position Limit Violation (July 21, 2025)

### The Problem
On July 21, 2025, GNS exceeded its configured `max_position_size` of 16,000 shares, reaching 17,700 shares. This violated the position limits despite having protective logic in place.

### Root Cause: Dual Position Tracking System
The system was maintaining **two separate position tracking mechanisms**:

1. **Internal Position** (`position.size`) - Calculated from `sum(trade.size for trade in self.trades.values())`
2. **TWS Position** (`position.true_share_count`) - Direct updates from Interactive Brokers TWS

### The Race Condition
The race condition occurred because:

1. **Order placement decisions** used `position.size` (internal tracking)
2. **Position limit violation detection** used `position.true_share_count` (TWS tracking)
3. **Internal position sync was lagging** ~30 seconds behind TWS updates

### Timeline of the GNS Violation:
```
12:01:01 - GNS trader created with max_position_size: 16000
12:01:02 - Order ID 1 placed (8k shares) - Internal position: 0, TWS: 0
12:01:03 - Order ID 1 partial fills: TWS shows 4,400 shares, Internal still 0
12:01:07 - Order ID 2 placed (8k shares) - System thinks position is still 0
12:01:12 - Order ID 3 placed (8k shares) - System thinks position is still 0
12:01:17 - Order ID 4 placed (8k shares) - System thinks position is still 0
12:01:22 - Order ID 3 FILLS completely (8k shares) - Internal finally syncs to 13,600
12:01:27 - Order ID 8 placed (2.4k shares) - Now using correct reduced size
12:01:32 - Order ID 9 placed (2.4k shares) - Still using reduced size
12:01:33 - VIOLATION: TWS position suddenly shows 17,700 shares
```

### The Fix: Single Source of Truth
Eliminated the dual tracking system by making **TWS position the single source of truth** for all position limit decisions:

#### Files Modified:

**1. `src/logic/order.py` - `handle_prediction()`**
```python
# OLD: Used internal position
if self.position.size >= self.assignment.max_position_size:

# NEW: Uses TWS position
current_tws_position = abs(self.position.true_share_count)
if current_tws_position >= self.assignment.max_position_size:
```

**2. `src/logic/order.py` - `place_order()`**
```python
# OLD: Dual checks with internal + TWS positions
current_position = abs(self.position.size)
current_tws_position = abs(self.position.true_share_count)

# NEW: Single check with TWS position only
current_tws_position = abs(self.position.true_share_count)
projected_tws_position = current_tws_position + size
```

**3. `src/logic/order.py` - `handle_max_position_size_check()`**
```python
# OLD: Checked both internal and TWS positions separately
current_size = self.position.size
tws_size = self.position.true_share_count

# NEW: Uses only TWS position
tws_size = abs(self.position.true_share_count)
```

**4. `src/logic/predicate.py` - `PositionSizePredicate`**
```python
# OLD: Used internal position
return position.size < self.assignment.max_position_size

# NEW: Uses TWS position
current_tws_position = abs(position.true_share_count)
return current_tws_position < self.assignment.max_position_size
```

### Why This Fixes the Race Condition:
1. **Eliminates sync lag** - No more waiting for internal position to catch up
2. **Real-time accuracy** - TWS position updates immediately when orders fill
3. **Consistent logic** - All position limit checks use the same data source
4. **Prevents over-ordering** - Order placement blocked immediately when TWS position reaches limit

### Internal Position Tracking Still Exists For:
- Trade lifecycle management (entry/exit pairing)
- P&L calculations
- Stop loss and take profit logic
- Historical trade records

The internal tracking is still valuable for trade management, but **position limits now rely exclusively on TWS data** to prevent race conditions.

### Testing Recommendation:
Test this fix with a low `max_position_size` (e.g., 1000 shares) and rapid order placement to ensure the race condition is eliminated. 