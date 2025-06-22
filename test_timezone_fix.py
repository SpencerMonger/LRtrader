#!/usr/bin/env python3
"""
Test script to verify the timezone fix for emergency exit orders.
This script demonstrates the difference between the old buggy logic and the new fixed logic.
"""

from datetime import datetime, time
import pytz

def test_timezone_logic():
    """Test both old (buggy) and new (fixed) timezone logic."""
    
    print("=" * 60)
    print("TIMEZONE FIX TEST")
    print("=" * 60)
    
    # Current system time (should be UTC)
    system_time = datetime.now()
    print(f"System time (naive): {system_time}")
    print(f"System timezone info: {system_time.tzinfo}")
    
    # Current UTC time (explicit)
    utc_time = datetime.now(pytz.UTC)
    print(f"UTC time (explicit): {utc_time}")
    
    print("\n" + "-" * 40)
    print("OLD BUGGY LOGIC:")
    print("-" * 40)
    
    # OLD BUGGY WAY (what was causing the problem)
    eastern_old = pytz.timezone("US/Eastern")
    now_et_old = datetime.now(eastern_old)  # ❌ This is wrong!
    
    print(f"Eastern time (OLD/BUGGY): {now_et_old}")
    print(f"Eastern tzinfo (OLD): {now_et_old.tzinfo}")
    
    # Check RTH with old logic
    rth_start = time(9, 30)
    rth_end = time(16, 0)
    is_outside_rth_old = not (rth_start <= now_et_old.time() <= rth_end)
    
    print(f"RTH start: {rth_start}")
    print(f"RTH end: {rth_end}")
    print(f"Current ET time: {now_et_old.time()}")
    print(f"Is outside RTH (OLD): {is_outside_rth_old}")
    print(f"Would set outsideRth = {is_outside_rth_old}")
    
    print("\n" + "-" * 40)
    print("NEW FIXED LOGIC:")
    print("-" * 40)
    
    # NEW CORRECT WAY (the fix we applied)
    eastern_new = pytz.timezone("US/Eastern")
    now_utc_new = datetime.now(pytz.UTC)
    now_et_new = now_utc_new.astimezone(eastern_new)  # ✅ This is correct!
    
    print(f"UTC time: {now_utc_new}")
    print(f"Eastern time (NEW/FIXED): {now_et_new}")
    print(f"Eastern tzinfo (NEW): {now_et_new.tzinfo}")
    
    # Check RTH with new logic
    is_outside_rth_new = not (rth_start <= now_et_new.time() <= rth_end)
    
    print(f"RTH start: {rth_start}")
    print(f"RTH end: {rth_end}")
    print(f"Current ET time: {now_et_new.time()}")
    print(f"Is outside RTH (NEW): {is_outside_rth_new}")
    print(f"Would set outsideRth = {is_outside_rth_new}")
    
    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY:")
    print("=" * 60)
    
    print(f"OLD logic thinks ET time is: {now_et_old.time()}")
    print(f"NEW logic thinks ET time is: {now_et_new.time()}")
    
    # Fix timezone comparison
    time_diff_seconds = abs((now_et_old.replace(tzinfo=None) - now_et_new.replace(tzinfo=None)).total_seconds())
    print(f"Time difference: {time_diff_seconds} seconds")
    
    if is_outside_rth_old != is_outside_rth_new:
        print("\n🚨 CRITICAL DIFFERENCE DETECTED!")
        print(f"OLD logic: outsideRth = {is_outside_rth_old}")
        print(f"NEW logic: outsideRth = {is_outside_rth_new}")
        print("This explains why emergency exits weren't working in pre-market!")
    else:
        print("\n✅ Both logics agree on RTH status")
    
    print("\n" + "-" * 60)
    print("EMERGENCY EXIT SCENARIO TEST:")
    print("-" * 60)
    
    # Simulate the 13:25 UTC scenario (9:25 AM ET)
    test_utc = datetime(2024, 1, 15, 13, 25, 0, tzinfo=pytz.UTC)  # 13:25 UTC
    test_et = test_utc.astimezone(eastern_new)  # Convert to ET
    
    print(f"Test scenario: {test_utc} UTC")
    print(f"Converts to: {test_et} ET")
    print(f"ET time only: {test_et.time()}")
    
    # Check if this would be outside RTH
    test_outside_rth = not (rth_start <= test_et.time() <= rth_end)
    print(f"Is 9:25 AM ET outside RTH? {test_outside_rth}")
    print(f"Emergency exit would set outsideRth = {test_outside_rth}")
    
    if test_outside_rth:
        print("✅ Emergency exits should now work in pre-market!")
    else:
        print("❌ Emergency exits would still wait for market open")

if __name__ == "__main__":
    test_timezone_logic() 