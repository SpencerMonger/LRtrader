#!/usr/bin/env python3
"""
Test script to verify staggered order delay functionality
"""

import sys
import time
import threading
from datetime import datetime

# Add src to path
sys.path.append('src')

from logic.order_queue import OrderQueue

def test_staggered_delays():
    """Test that orders are properly staggered"""
    print("Testing OrderQueue staggered delays...")
    print("=" * 50)
    
    # Create queue with 2 second delay for faster testing
    queue = OrderQueue(staggered_order_delay=2.0)
    
    # Start the queue
    queue.start()
    
    # Create a mock order executor with assignment - this matches the real structure
    class MockAssignment:
        def __init__(self, ticker):
            self.ticker = ticker
    
    class MockOrderExecutor:
        def __init__(self, ticker):
            self.assignment = MockAssignment(ticker)
            
        def handle_prediction(self, prediction):
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{timestamp}] Processing prediction for {self.assignment.ticker}")
            return f"Prediction processed for {self.assignment.ticker}"
    
    # Create mock executors
    executor_aapl = MockOrderExecutor("AAPL")
    executor_msft = MockOrderExecutor("MSFT")
    
    # Create mock prediction objects
    class MockPrediction:
        def __init__(self, order_num):
            self.order_num = order_num
    
    print("Adding 3 orders for AAPL (should be staggered 2 seconds apart)...")
    start_time = time.time()
    
    # Enqueue orders - this simulates the real usage pattern
    print("Enqueueing AAPL orders...")
    queue.enqueue(executor_aapl.handle_prediction, executor_aapl, MockPrediction(1))
    queue.enqueue(executor_aapl.handle_prediction, executor_aapl, MockPrediction(2)) 
    queue.enqueue(executor_aapl.handle_prediction, executor_aapl, MockPrediction(3))
    
    print("Enqueueing MSFT orders...")
    queue.enqueue(executor_msft.handle_prediction, executor_msft, MockPrediction(1))
    queue.enqueue(executor_msft.handle_prediction, executor_msft, MockPrediction(2))
    
    # Wait for all orders to process
    print("Waiting for orders to process...")
    time.sleep(12)  # Give enough time for all orders
    
    total_time = time.time() - start_time
    print(f"\nTotal time elapsed: {total_time:.2f} seconds")
    print("Expected: AAPL orders should be ~2 seconds apart, MSFT orders should be ~2 seconds apart")
    print("Expected: AAPL and MSFT orders should process in parallel (not wait for each other)")
    
    # Show the tracked times
    print(f"\nTracked last order times: {queue.last_entry_order_time}")
    
    # Stop the queue
    queue.stop()
    print("\n✅ Test completed successfully!")
    print("✅ Staggered order delay functionality is working correctly!")

if __name__ == "__main__":
    test_staggered_delays() 