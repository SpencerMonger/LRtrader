#!/usr/bin/env python3
"""
Test script to verify that orders are blocked when position limit is reached.
This tests the actual handle_prediction method and order placement logic.
"""

import sys
import os
sys.path.append('src')

from datetime import datetime
from schema.position import Position
from schema.assignment import TraderAssignment
from schema.enums import OrderAction, OrderType, OrderStatus
from schema.order import MongerOrder
from schema.prediction import Prediction, PriceDirection
from schema.market import MarketData, OrderBook
from logic.order import OrderExecutor
from loguru import logger
import time

def create_test_assignment():
    """Create a test assignment for TESTX ticker."""
    return TraderAssignment(
        ticker="TESTX",
        position_size=8000,
        max_position_size=16000,
        trade_threshold=60.0,
        hold_threshold=300.0,
        take_profit_target=0.02,
        stop_loss_target=0.01,
        stop_loss_strat="STATIC",
        spread_strategy="BEST",
        spread_offset=0.0,
        max_loss_per_trade=500.0,
        max_loss_cumulative=2000.0,
        clip_activation=0.005,
        clip_stop_loss=0.01
    )

def create_test_market_data():
    """Create a test market data object."""
    order_book = OrderBook()
    order_book.bid_price = 2.48
    order_book.ask_price = 2.52
    order_book.last_price = 2.50
    order_book.bid_size = 1000.0
    order_book.ask_size = 1000.0
    
    return MarketData(order_book=order_book)

class MockTWSApp:
    """Mock TWS application for testing."""
    def __init__(self):
        self.is_active = True
        self.orders_placed = []
        self.next_order_id = 1
    
    def nextOrderId(self):
        """Mock next order ID method."""
        return self.next_order_id
    
    def placeOrder(self, order_id, contract, order):
        """Mock order placement."""
        self.orders_placed.append({
            'order_id': order_id,
            'action': order.action,
            'size': order.totalQuantity,
            'limit_price': order.lmtPrice,
            'order_type': order.orderType
        })
        
        print(f"📋 PLACED ORDER {order_id}: {order.action} {order.totalQuantity} shares @ ${order.lmtPrice}")
        self.next_order_id += 1
        return order_id
    
    def cancelOrder(self, order_id, cancel_time):
        """Mock order cancellation."""
        print(f"❌ CANCELLED ORDER {order_id}")

def test_position_limit_blocking():
    """Test that orders are blocked when position limit is reached."""
    
    print("=" * 80)
    print("TESTING POSITION LIMIT BLOCKING IN handle_prediction")
    print("=" * 80)
    
    # Create test environment
    assignment = create_test_assignment()
    market_data = create_test_market_data()
    mock_tws = MockTWSApp()
    
    # Create position first
    position = Position(assignment=assignment)
    
    # Create order executor with correct parameters
    executor = OrderExecutor(position, assignment, market_data)
    
    # CRITICAL: Initialize the executor with the mock TWS app
    executor.initialize(mock_tws)
    
    print(f"Initial Position: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 1: Place first order when position is 0
    print("🧪 TEST 1: Place order when position = 0")
    prediction1 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction1)
    
    # Wait for the staggered queue to process
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)  # Wait for 5s stagger + 1s buffer
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    
    # Simulate the first order filling
    print("📈 Simulating first order fill...")
    if mock_tws.orders_placed:
        order_id = mock_tws.orders_placed[0]['order_id']
        size = mock_tws.orders_placed[0]['size']
        executor.position.handle_filled(order_id, size, 2.50)
        print(f"Position after fill: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 2: Place second order when position = 8000
    print("🧪 TEST 2: Place order when position = 8000")
    prediction2 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction2)
    
    # Wait for the staggered queue to process
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)  # Wait for 5s stagger + 1s buffer
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    
    # Simulate the second order filling
    print("📈 Simulating second order fill...")
    if len(mock_tws.orders_placed) >= 2:
        order_id = mock_tws.orders_placed[1]['order_id']
        size = mock_tws.orders_placed[1]['size']
        executor.position.handle_filled(order_id, size, 2.50)
        print(f"Position after fill: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 3: Try to place third order when position = 16000 (at limit)
    print("🧪 TEST 3: Try to place order when position = 16000 (AT LIMIT)")
    prediction3 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction3)
    
    # Wait for the staggered queue to process
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)  # Wait for 5s stagger + 1s buffer
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 4: Try to place fourth order when position = 16000 (should be blocked)
    print("🧪 TEST 4: Try to place another order when position = 16000 (SHOULD BE BLOCKED)")
    prediction4 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction4)
    
    # Wait for the staggered queue to process
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)  # Wait for 5s stagger + 1s buffer
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Shutdown the executor to stop the queue
    executor.shutdown()
    
    # Summary
    print("=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    print(f"Total orders placed: {len(mock_tws.orders_placed)}")
    print(f"Final position: {executor.position.size}/{assignment.max_position_size}")
    
    # Expected: Only 2 orders should be placed (8000 + 8000 = 16000)
    if len(mock_tws.orders_placed) == 2:
        print("✅ SUCCESS: Position limit blocking is working!")
        print("   - Only 2 orders were placed, reaching exactly the 16000 limit")
    else:
        print("❌ FAILURE: Position limit blocking is NOT working!")
        print(f"   - Expected 2 orders, but {len(mock_tws.orders_placed)} were placed")
    
    print()
    print("Orders placed:")
    for i, order in enumerate(mock_tws.orders_placed, 1):
        print(f"  {i}. Order {order['order_id']}: {order['action']} {order['size']} @ ${order['limit_price']}")

def test_position_limit_with_partial_fills():
    """Test that position limit blocking works correctly with partial fills."""
    
    print("=" * 80)
    print("TESTING POSITION LIMIT BLOCKING WITH PARTIAL FILLS")
    print("=" * 80)
    
    # Create test environment
    assignment = create_test_assignment()
    market_data = create_test_market_data()
    mock_tws = MockTWSApp()
    
    # Create position first
    position = Position(assignment=assignment)
    
    # Create order executor with correct parameters
    executor = OrderExecutor(position, assignment, market_data)
    
    # CRITICAL: Initialize the executor with the mock TWS app
    executor.initialize(mock_tws)
    
    print(f"Initial Position: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 1: Place first order (8000 shares)
    print("🧪 TEST 1: Place first order (8000 shares)")
    prediction1 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction1)
    
    # Wait for processing
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    
    # Simulate PARTIAL fill of first order (only 3000 out of 8000 shares)
    print("📈 Simulating PARTIAL fill of first order (3000/8000 shares)...")
    if mock_tws.orders_placed:
        order_id = mock_tws.orders_placed[0]['order_id']
        executor.position.handle_filled(order_id, 3000, 2.50)  # Only 3000 shares filled
        print(f"Position after partial fill: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 2: Place second order (8000 shares) - should be allowed since position is only 3000
    print("🧪 TEST 2: Place second order (8000 shares) - position is 3000, should be allowed")
    prediction2 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction2)
    
    # Wait for processing
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    
    # Simulate PARTIAL fill of second order (only 6000 out of 8000 shares)
    print("📈 Simulating PARTIAL fill of second order (6000/8000 shares)...")
    if len(mock_tws.orders_placed) >= 2:
        order_id = mock_tws.orders_placed[1]['order_id']
        executor.position.handle_filled(order_id, 6000, 2.50)  # Only 6000 shares filled
        print(f"Position after partial fill: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 3: Place third order (8000 shares) - should be allowed since position is 9000
    print("🧪 TEST 3: Place third order (8000 shares) - position is 9000, should be allowed")
    prediction3 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction3)
    
    # Wait for processing
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    
    # Simulate PARTIAL fill of third order (only 7000 out of 8000 shares to reach limit)
    print("📈 Simulating PARTIAL fill of third order (7000/8000 shares to reach 16000 limit)...")
    if len(mock_tws.orders_placed) >= 3:
        order_id = mock_tws.orders_placed[2]['order_id']
        executor.position.handle_filled(order_id, 7000, 2.50)  # 7000 shares to reach limit
        print(f"Position after partial fill: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Test 4: Try to place fourth order - should be BLOCKED since position is at 16000 limit
    print("🧪 TEST 4: Try to place fourth order - position is 16000 (AT LIMIT), should be BLOCKED")
    prediction4 = Prediction(flag=PriceDirection.BULLISH, confidence=0.8)
    executor.handle_prediction(prediction4)
    
    # Wait for processing
    print("⏳ Waiting for staggered queue to process...")
    time.sleep(6)
    
    print(f"Orders placed: {len(mock_tws.orders_placed)}")
    print(f"Position after: {executor.position.size}/{assignment.max_position_size}")
    print()
    
    # Shutdown the executor to stop the queue
    executor.shutdown()
    
    # Summary
    print("=" * 80)
    print("PARTIAL FILLS TEST RESULTS")
    print("=" * 80)
    print(f"Total orders placed: {len(mock_tws.orders_placed)}")
    print(f"Final position: {executor.position.size}/{assignment.max_position_size}")
    
    # Expected: 3 orders should be placed (stopped when position reached limit)
    if len(mock_tws.orders_placed) == 3:
        print("✅ SUCCESS: Position limit blocking works correctly with partial fills!")
        print("   - Orders were placed until position reached the 16000 limit")
        print("   - Fourth order was correctly blocked when limit was reached")
    else:
        print("❌ FAILURE: Position limit blocking is NOT working with partial fills!")
        print(f"   - Expected 3 orders, but {len(mock_tws.orders_placed)} were placed")
    
    print()
    print("Orders placed:")
    for i, order in enumerate(mock_tws.orders_placed, 1):
        print(f"  {i}. Order {order['order_id']}: {order['action']} {order['size']} @ ${order['limit_price']}")
    
    print()
    print("Fill sequence:")
    print("  1. Order 1: 3000 shares (partial) → Position: 3000")
    print("  2. Order 2: 6000 shares (partial) → Position: 9000")  
    print("  3. Order 3: 7000 shares (partial) → Position: 16000 (AT LIMIT)")
    print("  4. Order 4: BLOCKED (position at limit)")

if __name__ == "__main__":
    test_position_limit_with_partial_fills() 