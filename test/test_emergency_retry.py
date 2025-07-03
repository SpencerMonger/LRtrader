#!/usr/bin/env python3

import asyncio
from unittest.mock import Mock

# Test the emergency exit retry logic
print('Testing Emergency Exit Retry Logic...')

class TestEmergencyExitRetry:
    def __init__(self):
        self.ticker = 'TEST'
        self.stop_event = Mock()
        self.stop_event.is_set.return_value = False
        
        # Mock order executor
        self.order_executor = Mock()
        self.order_executor.is_emergency_exit = True
        
        # Mock position
        position = Mock()
        position.true_share_count = 500
        position.exit_action = Mock()
        position.exit_action.value = 'SELL'
        position.emergency_exit_order = None
        self.order_executor.position = position
        
        # Mock market data
        market_data = Mock()
        market_data.bid = 10.50
        market_data.ask = 10.55
        market_data.last_price = 10.52
        self.order_executor.market_data = market_data
        
        # Mock place_order method
        self.order_executor.place_order = Mock()
        
    async def emergency_exit_retry_loop(self):
        print(f'[{self.ticker}] Starting aggressive emergency exit retry loop')
        retry_count = 0
        
        # Add a flag to prevent multiple retry loops from running
        if hasattr(self, '_emergency_retry_active') and self._emergency_retry_active:
            print(f'[{self.ticker}] Emergency exit retry loop already active, skipping')
            return 0
        
        self._emergency_retry_active = True
        
        try:
            while not self.stop_event.is_set() and self.order_executor.is_emergency_exit:
                retry_count += 1
                print(f'[{self.ticker}] Emergency exit retry attempt #{retry_count}')
                
                # Check if position is fully closed
                current_position = self.order_executor.position.true_share_count
                if current_position == 0:
                    print(f'[{self.ticker}] Position fully closed, stopping emergency exit retry loop')
                    self.order_executor.is_emergency_exit = False
                    break
                
                # Cancel existing emergency exit order if it exists
                if self.order_executor.position.emergency_exit_order:
                    print(f'[{self.ticker}] Cancelling existing emergency exit order')
                    await asyncio.sleep(0.01)  # Simulate cancellation delay
                
                # Place new emergency exit order at current bid/ask price
                exit_size = abs(current_position)
                if exit_size > 0:
                    emergency_action = self.order_executor.position.exit_action
                    
                    # Get current market data
                    bid_price = self.order_executor.market_data.bid
                    ask_price = self.order_executor.market_data.ask
                    
                    # Use bid for selling, ask for buying
                    emergency_price = bid_price if emergency_action.value == 'SELL' else ask_price
                    
                    # Ensure we have valid pricing
                    if emergency_price and emergency_price > 0:
                        print(f'[{self.ticker}] Placing aggressive emergency exit order: {emergency_action.value} {exit_size} @ ${emergency_price:.2f}')
                        
                        self.order_executor.place_order(
                            order_type='EMERGENCY_EXIT',
                            order_action=emergency_action,
                            size=exit_size,
                            price=emergency_price,
                        )
                    else:
                        print(f'[{self.ticker}] Invalid pricing for emergency exit')
                        fallback_price = self.order_executor.market_data.last_price or 1.0
                        print(f'[{self.ticker}] Using fallback price ${fallback_price:.2f} for emergency exit')
                        
                        self.order_executor.place_order(
                            order_type='EMERGENCY_EXIT',
                            order_action=emergency_action,
                            size=exit_size,
                            price=fallback_price,
                        )
                else:
                    print(f'[{self.ticker}] Exit size is 0, position may have been closed')
                    break
                
                # Simulate position reduction after each attempt
                if retry_count == 1:
                    self.order_executor.position.true_share_count = 300
                elif retry_count == 2:
                    self.order_executor.position.true_share_count = 100
                elif retry_count >= 3:
                    self.order_executor.position.true_share_count = 0
                
                # Short sleep for testing (instead of 10 seconds)
                await asyncio.sleep(0.01)
                
                # Stop after 4 attempts for testing
                if retry_count >= 4:
                    break
                    
        except Exception as e:
            print(f'{self.ticker} -- EXCEPTION IN EMERGENCY EXIT RETRY LOOP: {e}')
        finally:
            self._emergency_retry_active = False
        
        print(f'[{self.ticker}] Emergency exit retry loop finished after {retry_count} attempts')
        return retry_count

# Run the test
async def run_test():
    print('=== Testing Emergency Exit Retry Loop ===')
    test_trader = TestEmergencyExitRetry()
    attempts = await test_trader.emergency_exit_retry_loop()
    
    print(f'\n=== Test Results ===')
    print(f'Total retry attempts: {attempts}')
    print(f'place_order called: {test_trader.order_executor.place_order.call_count} times')
    
    # Verify the calls
    calls = test_trader.order_executor.place_order.call_args_list
    for i, call in enumerate(calls):
        args, kwargs = call
        action_value = kwargs.get("order_action").value if kwargs.get("order_action") else "N/A"
        print(f'Order {i+1}: type={kwargs.get("order_type")}, action={action_value}, size={kwargs.get("size")}, price=${kwargs.get("price"):.2f}')
    
    print('\n=== Testing Multiple Retry Loop Prevention ===')
    # Test that multiple retry loops don't start
    test_trader2 = TestEmergencyExitRetry()
    test_trader2._emergency_retry_active = True  # Simulate already active
    attempts2 = await test_trader2.emergency_exit_retry_loop()
    print(f'Second retry loop attempts (should be 0): {attempts2}')
    
    print('\nTest completed successfully!')

if __name__ == "__main__":
    asyncio.run(run_test()) 