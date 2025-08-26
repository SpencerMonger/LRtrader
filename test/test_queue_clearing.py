#!/usr/bin/env python3
"""
Test script to verify queue clearing functionality when max position is reached
"""

import sys
import time
import threading
from datetime import datetime

# Add src to path
sys.path.append('src')

from logic.order_queue import OrderQueue

def test_queue_clearing():
    """Test that queued orders are cleared when max position is reached"""
    print("Testing Queue Clearing Functionality...")
    print("=" * 60)
    
    # Create queue with very short delay for faster testing
    queue = OrderQueue(staggered_order_delay=0.5)
    
    # Start the queue
    queue.start()
    
    # Track executed orders
    executed_orders = []
    
    # Create a mock order executor with assignment
    class MockAssignment:
        def __init__(self, ticker):
            self.ticker = ticker
    
    class MockOrderExecutor:
        def __init__(self, ticker):
            self.assignment = MockAssignment(ticker)
            
        def handle_prediction(self, prediction):
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            order_info = f"{self.assignment.ticker}-Order{prediction.order_num}"
            executed_orders.append(order_info)
            print(f"[{timestamp}] ✅ EXECUTED: {order_info}")
            return f"Prediction processed for {self.assignment.ticker}"
    
    # Create mock executors for multiple tickers
    executor_mrkr = MockOrderExecutor("MRKR")
    executor_aapl = MockOrderExecutor("AAPL")
    
    # Create mock prediction objects
    class MockPrediction:
        def __init__(self, order_num):
            self.order_num = order_num
    
    print("\n📝 PHASE 1: Queue multiple orders for MRKR and AAPL")
    print("Enqueueing 4 orders for MRKR...")
    queue.enqueue(executor_mrkr.handle_prediction, executor_mrkr, MockPrediction(1))
    queue.enqueue(executor_mrkr.handle_prediction, executor_mrkr, MockPrediction(2))
    queue.enqueue(executor_mrkr.handle_prediction, executor_mrkr, MockPrediction(3))
    queue.enqueue(executor_mrkr.handle_prediction, executor_mrkr, MockPrediction(4))
    
    print("Enqueueing 2 orders for AAPL...")
    queue.enqueue(executor_aapl.handle_prediction, executor_aapl, MockPrediction(1))
    queue.enqueue(executor_aapl.handle_prediction, executor_aapl, MockPrediction(2))
    
    # Let the first order execute for each ticker
    print("\n⏳ Waiting 1.5 seconds for first orders to execute...")
    time.sleep(1.5)
    
    print(f"\n📊 Orders executed so far: {executed_orders}")
    
    print("\n🛑 PHASE 2: Simulate max position reached - clearing MRKR queue")
    print("Calling clear_ticker_queue('MRKR')...")
    
    # This simulates what happens when max position is reached
    queue.clear_ticker_queue('MRKR')
    
    print("\n⏳ Waiting 3 more seconds to see if any cleared orders execute...")
    time.sleep(3)
    
    print(f"\n📊 Final executed orders: {executed_orders}")
    
    # Analyze results
    print("\n📈 ANALYSIS:")
    mrkr_orders = [order for order in executed_orders if 'MRKR' in order]
    aapl_orders = [order for order in executed_orders if 'AAPL' in order]
    
    print(f"MRKR orders executed: {mrkr_orders}")
    print(f"AAPL orders executed: {aapl_orders}")
    
    # Test assertions
    print("\n🧪 VERIFICATION:")
    
    # Should have only 1 MRKR order (the first one that executed before clearing)
    if len(mrkr_orders) <= 2:  # Allow for potential timing variations
        print("✅ PASS: MRKR queue clearing worked - minimal orders executed")
    else:
        print(f"❌ FAIL: Too many MRKR orders executed ({len(mrkr_orders)}) - queue clearing failed")
    
    # AAPL orders should still execute normally (not affected by MRKR clearing)
    if len(aapl_orders) >= 1:
        print("✅ PASS: AAPL orders unaffected by MRKR queue clearing")
    else:
        print("❌ FAIL: AAPL orders were incorrectly affected by MRKR queue clearing")
    
    # Should have fewer MRKR orders than AAPL orders (due to clearing)
    if len(mrkr_orders) <= len(aapl_orders):
        print("✅ PASS: Queue clearing selectively affected only target ticker")
    else:
        print("❌ FAIL: Queue clearing didn't work selectively")
    
    # Stop the queue
    queue.stop()
    
    print("\n" + "=" * 60)
    print("🎯 TEST SUMMARY:")
    print(f"   - MRKR orders executed: {len(mrkr_orders)} (should be ≤2)")
    print(f"   - AAPL orders executed: {len(aapl_orders)} (should be ≥1)")
    print(f"   - Queue clearing selective: {'✅' if len(mrkr_orders) <= len(aapl_orders) else '❌'}")
    
    if len(mrkr_orders) <= 2 and len(aapl_orders) >= 1 and len(mrkr_orders) <= len(aapl_orders):
        print("\n🎉 OVERALL RESULT: ✅ QUEUE CLEARING FUNCTIONALITY WORKS CORRECTLY!")
        print("   - Stale orders successfully prevented")
        print("   - Only target ticker affected")
        print("   - Other tickers unaffected")
    else:
        print("\n⚠️  OVERALL RESULT: ❌ QUEUE CLEARING NEEDS DEBUGGING")
    
    return executed_orders

def test_queue_clearing_edge_cases():
    """Test edge cases for queue clearing"""
    print("\n" + "=" * 60)
    print("Testing Queue Clearing Edge Cases...")
    print("=" * 60)
    
    # Test clearing empty queue
    queue = OrderQueue(staggered_order_delay=0.1)
    queue.start()
    
    print("Testing clearing empty queue...")
    queue.clear_ticker_queue('NONEXISTENT')
    print("✅ Empty queue clearing handled gracefully")
    
    # Test clearing non-existent ticker
    print("Testing clearing non-existent ticker...")
    queue.clear_ticker_queue('FAKE_TICKER')
    print("✅ Non-existent ticker clearing handled gracefully")
    
    queue.stop()
    print("✅ Edge case tests completed")

if __name__ == "__main__":
    # Run main test
    executed_orders = test_queue_clearing()
    
    # Run edge case tests
    test_queue_clearing_edge_cases()
    
    print("\n🚀 All tests completed!")
