# Monger Trading Strategy

Based on the provided `order.py` file, your trading strategy is meticulously
designed to manage trade executions, monitor market conditions, and handle
various order statuses. Here's a comprehensive breakdown of your strategy:

## 1. **Order Types**

Your strategy utilizes distinct order types to manage different aspects of
trading. These orders ensure precise control over entries, exits, and risk
management.

- **ENTRY**: Initiates a new position by entering the market.
- **TAKE_PROFIT**: Attempts to lock in profits by closing a portion of the
  position once a target is reached.
- **STOP_LOSS**: Limits potential losses by closing a portion if price moves
  unfavorably.
- **EXIT**: Closes a position entirely.
- **EMERGENCY_EXIT**: Forces a rapid exit from all open positions in urgent
  situations.
- **DANGLING_SHARES**: Corrects any mismatch between the broker’s true share
  count and the system’s internal share count by buying or selling the
  difference.

## 2. **Order Creation Mechanism**

The `OrderFactory` class is responsible for creating various types of orders. It
ensures that each order is configured correctly based on its purpose.

- **Market Orders**: Execute immediately at the current market price.
- **Limit Orders**: Execute at a specified price or better.
- **Stop Limit Orders**: Combine stop and limit orders to trigger a limit order
  when a certain price is reached.
- **Entry Limit Orders**: Specifically designed for initiating positions with
  predefined take profit and stop loss parameters.

> Note: While “Market Orders,” “Limit Orders,” and “Stop Limit Orders” are
> referenced, the current code mostly places limit orders for STOP_LOSS,
> TAKE_PROFIT, and ENTRY. There is no explicit “Stop Limit” order. Market orders
> are not generally placed. “Entry Limit Orders” are effectively limit orders
> with a set limit_price.

## 3. **Order Execution Workflow**

### a. **Handling Predictions**

- **Prediction Assessment**: The strategy begins by evaluating market
  predictions. If the prediction meets certain confidence and position size
  predicates, it proceeds to execute an entry.
- **Entry Execution**:
  - **Action Determination**: Decides whether to `BUY` or `SELL` based on the
    prediction's direction (bullish or bearish).
  - **Order Placement**: Creates and places an entry limit order with predefined
    take profit and stop loss parameters.
- **Take Profit and Stop Loss Setup**:
  - **Take Profit**: Sets a target price to sell a portion of the position to
    secure profits.
  - **Stop Loss**: Sets a threshold to sell a portion of the position to
    minimize losses.

### b. **Managing Order Status Updates**

- **Filled Orders**:

  - **Entry Orders**: Upon filling, updates the position and ensures
    corresponding take profit and stop loss orders are in place or updated
    accordingly.
  - **Exit Orders**: Adjusts the position based on the filled exit and manages
    take profit and stop loss orders to reflect the new position state.
  - **Take Profit Orders**: Once filled, calculates realized profits, cancels or
    adjusts other related orders, and sets new stop loss orders to protect
    remaining positions.
  - **Stop Loss Orders**: Upon filling, limits losses by adjusting the position,
    cancels related orders, and may trigger additional exit orders if needed.
  - **Emergency Exit Orders**: Ensures all positions are closed swiftly by
    canceling open orders and placing necessary exit orders.

- **Cancelled Orders**:
  - **Entry Orders**: Removes references to canceled entries and updates the
    position to reflect any partially filled quantities.
  - **Exit Orders**: Re-submits exit orders with remaining quantities to ensure
    the position is adequately managed.
  - **Take Profit and Stop Loss Orders**: Updates the position to reflect the
    cancellation and removes any invalid references.

### c. **Market Data Integration**

- **Stop Loss Adjustment**: The system uses a static offset for placing
  stop-loss orders. The strategy recalculates and re-places these orders
  whenever market data updates, rather than tracking a trailing high/low.

### d. **Handling Expired Positions**

- **Position Expiration**: Checks for positions that have exceeded a maximum
  hold time. If found, it places immediate exit orders to close these positions,
  ensuring that outdated trades do not linger and affect the overall strategy.
- **Stop Loss Cooldown**: Whenever a stop-loss order triggers, the strategy
  enforces a brief cooldown period (e.g. 60 seconds) during which certain new
  orders are disallowed.

## 4. **Emergency Exits**

- **Triggering Emergency Exits**: In critical situations, the strategy can
  trigger an emergency exit to close all open positions swiftly. This involves:
  - **Cancelling Open Orders**: Ensures that no additional orders interfere with
    the emergency exit.
  - **Placing Exit Orders**: Executes exit orders at the current best available
    price to liquidate the position promptly.
  - **Resetting State**: Updates internal flags to indicate that an emergency
    exit is in progress or completed.

## 5. **Predicate Checks and Position Scaling**

- **Predicate Evaluation**: Before executing any trades, the strategy evaluates
  certain predicates to determine if the conditions are favorable for entering
  or adjusting a position. These predicates include:
  - **Confidence Threshold**: Ensures that the prediction confidence is above a
    certain threshold.
  - **Position Size Limits**: Checks if the current position size adheres to
    predefined limits to manage risk effectively.
- **Position Scaling**: The system always returns a scale factor of 1.0. No
  dynamic scaling is performed.
- **Clip Logic**: The strategy references “clip_activation” and “clip_stop_loss”
  but does not actively use them.

## 6. **Position Management**

- **Unit Position Tracking**: Maintains detailed records of each unit within a
  position, including order IDs, sides (long or short), sizes, filled
  quantities, and average prices.
- **Trade Association**: Groups related executions into trades, managing their
  lifecycle from entry to exit. This association facilitates accurate tracking
  of performance and risk metrics.
- **Dangling Shares Protocol**: If the live brokerage position count doesn’t
  match the internal position count, the system can generate a DANGLING_SHARES
  order to reconcile the difference.

## 7. Manual Orders

The strategy also accommodates manual orders placed directly through the
Interactive Brokers Trader Workstation (TWS). In particular, the
PortfolioManager binds to TWS client ID 0, allowing it to receive order status
updates for manually placed orders. Whenever a manual order is filled, the
PortfolioManager propagates those changes to the corresponding TradeMonger
thread, ensuring that the system’s internal position and trade records stay
consistent with any externally executed trades.
