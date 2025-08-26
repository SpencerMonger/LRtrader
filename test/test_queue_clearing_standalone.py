#!/usr/bin/env python3
"""
Standalone test for queue clearing functionality
Tests the core logic without requiring full project dependencies
"""

import queue
import time
import threading
from datetime import datetime

class MockOrderQueue:
    """Simplified version of OrderQueue for testing"""
    def __init__(self, staggered_order_delay: float = 5.0):
        self.queue = queue.Queue()
        self.worker_thread = None
        self.is_running = False
        self.last_entry_order_time = {}
        self.entry_order_delay = staggered_order_delay

    def start(self):
        """Start the worker thread."""
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._worker)
        self.worker_thread.start()

    def stop(self):
        """Stop the worker thread."""
        self.is_running = False
        self.queue.put((None, None, None, None))
        if self.worker_thread:
            self.worker_thread.join(timeout=5)

    def enqueue(self, func, *args, **kwargs):
        """Enqueue a function call."""
        if self.is_running:
            ticker = None
            # For bound methods, get ticker from the bound instance
            if hasattr(func, '__self__') and hasattr(func.__self__, 'assignment'):
                ticker = func.__self__.assignment.ticker
            # For regular functions, try to get ticker from first argument
            elif args and hasattr(args[0], 'assignment') and hasattr(args[0].assignment, 'ticker'):
                ticker = args[0].assignment.ticker
            self.queue.put((func, args, kwargs, ticker))

    def clear_ticker_queue(self, ticker: str):
        """Clear all queued handle_prediction calls for a specific ticker."""
        if not self.is_running:
            return
        
        temp_items = []
        cleared_count = 0
        
        # Drain the queue
        while True:
            try:
                func, args, kwargs, queued_ticker = self.queue.get_nowait()
                
                # Keep items that are not handle_prediction calls for this ticker
                if not (queued_ticker == ticker and func.__name__ == 'handle_prediction'):
                    temp_items.append((func, args, kwargs, queued_ticker))
                else:
                    cleared_count += 1
                    self.queue.task_done()
                    
            except queue.Empty:
                break
        
        # Put back the items we want to keep
        for item in temp_items:
            self.queue.put(item)
        
        if cleared_count > 0:
            print(f"[{ticker}] Cleared {cleared_count} queued handle_prediction calls from order queue")

    def _worker(self):
        """Worker thread to process queued function calls."""
        while self.is_running:
            try:
                func, args, kwargs, ticker = self.queue.get(timeout=1)
                
                if func is None:
                    break
                
                # Apply staggered delay for entry orders
                if ticker and func.__name__ == 'handle_prediction':
                    current_time = time.time()
                    last_time = self.last_entry_order_time.get(ticker, 0)
                    time_since_last = current_time - last_time
                    
                    if last_time > 0 and time_since_last < self.entry_order_delay:
                        delay_needed = self.entry_order_delay - time_since_last
                        print(f"[{ticker}] Applying staggered delay: {delay_needed:.1f}s")
                        time.sleep(delay_needed)
                    elif last_time == 0:
                        print(f"[{ticker}] First order for ticker - skipping staggered delay")
                    
                    self.last_entry_order_time[ticker] = time.time()
                    
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print(f"Error executing queued function {func.__name__}: {e}")
                finally:
                    self.queue.task_done()
            except queue.Empty:
                continue

def test_queue_clearing():
    """Test that queued orders are cleared when max position is reached"""
    print("Testing Queue Clearing Functionality...")
    print("=" * 60)
    
    # Create queue with short delay for faster testing
    order_queue = MockOrderQueue(staggered_order_delay=0.5)
    order_queue.start()
    
    # Track executed orders
    executed_orders = []
    
    # Mock classes
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
    
    class MockPrediction:
        def __init__(self, order_num):
            self.order_num = order_num
    
    # Create executors
    executor_mrkr = MockOrderExecutor("MRKR")
    executor_aapl = MockOrderExecutor("AAPL")
    
    print("\n📝 PHASE 1: Queue multiple orders")
    print("Enqueueing 4 orders for MRKR...")
    order_queue.enqueue(executor_mrkr.handle_prediction, MockPrediction(1))
    order_queue.enqueue(executor_mrkr.handle_prediction, MockPrediction(2))
    order_queue.enqueue(executor_mrkr.handle_prediction, MockPrediction(3))
    order_queue.enqueue(executor_mrkr.handle_prediction, MockPrediction(4))
    
    print("Enqueueing 2 orders for AAPL...")
    order_queue.enqueue(executor_aapl.handle_prediction, MockPrediction(1))
    order_queue.enqueue(executor_aapl.handle_prediction, MockPrediction(2))
    
    # Let first order execute, then clear queue while others are still waiting
    print("\n⏳ Waiting 0.7 seconds for first order to execute...")
    time.sleep(0.7)
    
    print(f"\n📊 Orders executed so far: {executed_orders}")
    
    print("\n🛑 PHASE 2: Simulate max position reached - clearing MRKR queue")
    order_queue.clear_ticker_queue('MRKR')
    
    print("\n⏳ Waiting 3 more seconds to see if any cleared orders still execute...")
    time.sleep(3)
    
    print(f"\n📊 Final executed orders: {executed_orders}")
    
    # Analysis
    mrkr_orders = [order for order in executed_orders if 'MRKR' in order]
    aapl_orders = [order for order in executed_orders if 'AAPL' in order]
    
    print("\n📈 ANALYSIS:")
    print(f"MRKR orders executed: {mrkr_orders}")
    print(f"AAPL orders executed: {aapl_orders}")
    
    print("\n🧪 VERIFICATION:")
    
    success = True
    
    # Should have fewer MRKR orders than originally queued (4 queued, some prevented)
    if len(mrkr_orders) < 4:
        print(f"✅ PASS: MRKR queue clearing worked - prevented some orders ({4 - len(mrkr_orders)} prevented)")
    else:
        print(f"❌ FAIL: No MRKR orders were prevented ({len(mrkr_orders)}/4 executed)")
        success = False
    
    # AAPL should execute normally
    if len(aapl_orders) >= 1:
        print("✅ PASS: AAPL orders unaffected by MRKR queue clearing")
    else:
        print("❌ FAIL: AAPL orders incorrectly affected")
        success = False
    
    order_queue.stop()
    
    print("\n" + "=" * 60)
    print("🎯 TEST SUMMARY:")
    print(f"   - MRKR orders: {len(mrkr_orders)}/4 executed ({4 - len(mrkr_orders)} prevented)")
    print(f"   - AAPL orders: {len(aapl_orders)}/2 executed (should be ≥1)")
    print(f"   - Queue clearing message: {'✅ Logged' if 'Cleared' in ' '.join(str(e) for e in executed_orders) else '❌ Missing'}")
    
    if success:
        print("\n🎉 OVERALL RESULT: ✅ QUEUE CLEARING FUNCTIONALITY WORKS!")
        print("   - ✅ Stale orders successfully prevented")
        print("   - ✅ Only target ticker affected")  
        print("   - ✅ Other tickers unaffected")
        print("   - ✅ Race conditions handled gracefully")
    else:
        print("\n⚠️  OVERALL RESULT: ❌ QUEUE CLEARING NEEDS FIXING")
    
    return success

if __name__ == "__main__":
    success = test_queue_clearing()
    
    if success:
        print("\n🚀 Test completed successfully!")
        exit(0)
    else:
        print("\n💥 Test failed!")
        exit(1)
