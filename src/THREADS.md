# Monger System Threading Guide

This document explains how the various threads are spawned and managed across
the application, along with how they integrate with the Interactive Brokers
(IBKR) API. It also describes how the system is started, run, and stopped in
both production and local environments.

## Overview

The system revolves around several core files that manage threaded operations:

1. **app.py**: Defines the main "TradeMonger" class, which implements the
   high-level order execution logic for a single ticker.

   - Each TradeMonger instance runs an asynchronous inference loop, a historical
     data loop, and a position monitoring loop in separate tasks.
   - It connects to IBKR Trader Workstation (TWS) or IB Gateway, receiving
     read/write events and placing orders. This is done using the Interactive
     Brokers client ID (passed when calling connect).
   - All logic related to responding to predictions, placing orders, and
     handling their statuses (fills, partial fills, cancellations) is maintained
     here.

2. **portfolio_app.py**: Defines the "PortfolioManager", also based on the IBKR
   API classes.

   - Binds specifically to TWS client ID 0, allowing it to receive and track
     manual orders that might be placed directly in TWS by a human operator.
   - Whenever a manual order's status changes, the PortfolioManager propagates
     that update back to the correct TradeMonger instance so that the internal
     position is kept consistent.
   - Takes account-level PnL updates, checks for thresholds, and can execute
     further manager-side logic if certain triggers are exceeded.

3. **manager.py**: Defines the "MongerManager" class, which supervises multiple
   TradeMonger instances plus one PortfolioManager.

   - Creates a background thread for each TradeMonger instance, passing a
     distinct client ID to the TWS or IB Gateway so that each ticker trades
     independently.
   - Creates a dedicated thread for the PortfolioManager with client ID 0 to
     capture manual orders.
   - Offers start() and stop() methods that manage all running threads in an
     orchestrated way, shutting down gracefully when needed.

4. **run_prod.py** and **run_local.py**:
   - Both define the application's entry point using the MongerManager described
     above.
   - They parse command-line arguments (host, port, account, config) and create
     a manager with those parameters.
   - They schedule or immediately call manager.start() to create and run all
     threads.
   - They handle signals (e.g., CTRL+C) to invoke manager.stop() gracefully.

## Thread and Task Lifecycle

### 1. Initialization

- The code (in run_local.py or run_prod.py) parses command-line arguments and
  loads the configuration file.
- A MongerManager is created with the loaded TraderAssignments plus an account
  ID, host, and port.
- The PortfolioManager is embedded in the MongerManager to handle manual orders.
  It is always connected with clientId=0.

### 2. Start

- When manager.start() is called (in an asyncio context), the manager creates an
  async task group (anyio.create_task_group) and spins up:
  - "run_monger" calls for each ticker's TradeMonger instance
  - "run_portfolio_manager" call for the single PortfolioManager instance
- Each of these (TradeMonger or PortfolioManager) then uses Python's
  anyio.to_thread.run_sync(...) to initiate a blocking call to .run(), which
  never returns until the user or system triggers a stop.
- The TradeMonger's .run() connects to TWS/IB Gateway on the provided (host,
  port, clientId) socket. Then it starts its own internal asynchronous loops for
  market data, predictions, and position monitoring.
- The PortfolioManager's .run() also connects, but specifically with clientId=0
  so it can receive and handle manual TWS orders.

### 3. During Execution

- TradeMonger tasks track market data, inference predictions, and open
  positions. Each task can spawn further logic (like placing orders or modifying
  them).
- The PortfolioManager receives manual orders from TWS, forwarding fills and
  cancellations to the relevant TradeMonger instance.
- Communication to TWS is done via the classes in the **clients** package, which
  basically wrap IBKR's EClient/EWrapper architecture. The relevant TWS
  documentation says that all events are funneled through the EWrapper callbacks
  (portfolio updates, executions, order status changes, etc.).

### 4. Stopping

- If a shutdown signal (SIGINT or SIGTERM) occurs, run_local.py or run_prod.py
  calls manager.stop() from an anyio task.
- The MongerManager then sets a running=False flag, calls .stop() on the
  PortfolioManager, plus .stop() on each TradeMonger in parallel.
- In each .stop(), any emergency exit logic is triggered, if configured.
  Positions are closed, orders are cancelled, and the final steps are completed.
  Then the manager's threads all end.
- In normal usage for local runs, simply pressing CTRL+C triggers a graceful
  stop. Under production usage with scheduling, the system attempts an
  auto-shutdown after certain times (within run_prod.py).

## Developer FAQ

### 1. Handling TWS/Gateway Disconnections

By default, if TWS/Gateway goes offline, the IB API might disconnect. The code
currently does not include auto-retry logic on reconnect. You can implement
reconnect logic in EWrapper callbacks or rely on an external supervisor to
restart the system if a connection drops.

### 2. Persisting State Across Runs

No built-in mechanism exists for persistent storage. Each run recreates
positions from real-time data. If you need long-lived data (e.g. historical
trades), store it in a separate DB or logs. On restart, the manager re-pulls
positions from TWS.

### 3. Thread-Safe Partial Fills

Each TradeMonger instance processes its own ticker's fill events. Partial fills
trigger EWrapper callbacks, updating the position in that ticker's thread. This
avoids cross-thread conflicts since no other thread directly modifies that
position data.

### 4. Client ID Management

The manager sets a unique clientId for each TradeMonger; portfolio_app.py
attaches to clientId=0 for manual orders. TWS can support multiple clientIds,
but large-scale usage might exceed practical IBKR limits. If that occurs, you
may need to reduce ticker count or batch requests.

### 5. Crash Recovery

If a thread crashes, the manager logs an error, but does not automatically
restart the thread. For production, an external process (e.g., Supervisor or
systemd) can monitor and relaunch the entire app. You could add internal logic
to re-initialize the manager upon a runtime exception.
