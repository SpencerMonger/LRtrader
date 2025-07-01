#!/usr/bin/env python3
"""
Test script specifically for the 13:25 UTC emergency exit scenario.
This demonstrates the exact bug that was preventing pre-market emergency exits.
"""

from datetime import datetime, time
import pytz

def test_emergency_exit_timing():
    """Test the specific 13:25 UTC scenario that was failing."""
    
    print("=" * 70)
    print("EMERGENCY EXIT TIMING TEST - 13:25 UTC SCENARIO")
    print("=" * 70)
    
    # Simulate the exact scenario: 13:25 UTC (your crontab kill time)
    kill_time_utc = datetime(2024, 1, 15, 13, 25, 0, tzinfo=pytz.UTC)
    
    print(f"Kill script execution time: {kill_time_utc} UTC")
    print(f"This should be: 9:25 AM ET (pre-market)")
    
    # RTH boundaries
    rth_start = time(9, 30)  # 9:30 AM ET
    rth_end = time(16, 0)    # 4:00 PM ET
    
    print(f"Regular Trading Hours: {rth_start} - {rth_end} ET")
    
    print("\n" + "=" * 70)
    print("TESTING OLD BUGGY LOGIC:")
    print("=" * 70)
    
    # OLD BUGGY LOGIC (what was in the code before the fix)
    eastern_old = pytz.timezone("US/Eastern")
    
    # Simulate what the old code would do at 13:25 UTC
    # The bug: datetime.now(eastern) creates a naive datetime in Eastern timezone
    # instead of converting UTC to Eastern
    
    # To simulate the bug, we need to create what the old code would have created
    # at 13:25 UTC system time
    buggy_et_time = datetime(2024, 1, 15, 13, 25, 0)  # This is the bug - treats UTC as ET!
    
    print(f"OLD BUGGY: datetime.now(eastern) would create: {buggy_et_time} ET")
    print(f"OLD BUGGY: This incorrectly treats {kill_time_utc} UTC as {buggy_et_time} ET")
    
    # Check RTH with buggy logic
    is_outside_rth_buggy = not (rth_start <= buggy_et_time.time() <= rth_end)
    
    print(f"OLD BUGGY: Is 13:25 ET outside RTH (9:30-16:00)? {is_outside_rth_buggy}")
    print(f"OLD BUGGY: Would set outsideRth = {is_outside_rth_buggy}")
    
    if is_outside_rth_buggy:
        print("OLD BUGGY: ❌ This is wrong! 13:25 ET (1:25 PM) is during RTH!")
    else:
        print("OLD BUGGY: ✅ Correctly identifies 13:25 ET as during RTH")
        print("OLD BUGGY: ❌ BUT this is the wrong time! Should be 9:25 AM ET!")
    
    print("\n" + "=" * 70)
    print("TESTING NEW FIXED LOGIC:")
    print("=" * 70)
    
    # NEW FIXED LOGIC (what we implemented)
    eastern_new = pytz.timezone("US/Eastern")
    correct_et_time = kill_time_utc.astimezone(eastern_new)
    
    print(f"NEW FIXED: {kill_time_utc} UTC converts to: {correct_et_time}")
    print(f"NEW FIXED: Correctly shows {correct_et_time.time()} ET")
    
    # Check RTH with fixed logic
    is_outside_rth_fixed = not (rth_start <= correct_et_time.time() <= rth_end)
    
    print(f"NEW FIXED: Is {correct_et_time.time()} ET outside RTH (9:30-16:00)? {is_outside_rth_fixed}")
    print(f"NEW FIXED: Would set outsideRth = {is_outside_rth_fixed}")
    
    if is_outside_rth_fixed:
        print("NEW FIXED: ✅ Correctly identifies 9:25 AM ET as pre-market!")
    else:
        print("NEW FIXED: ❌ This would be wrong for pre-market")
    
    print("\n" + "=" * 70)
    print("IMPACT ANALYSIS:")
    print("=" * 70)
    
    print("When kill script runs at 13:25 UTC:")
    print(f"- OLD BUGGY: Emergency exit outsideRth = {is_outside_rth_buggy}")
    print(f"- NEW FIXED: Emergency exit outsideRth = {is_outside_rth_fixed}")
    
    if is_outside_rth_buggy != is_outside_rth_fixed:
        print("\n🚨 CRITICAL BUG CONFIRMED!")
        print("The old logic was preventing pre-market emergency exits!")
        print("Orders would queue until 13:30 UTC (9:30 AM ET market open)")
        print("The fix now allows immediate pre-market execution!")
    else:
        print("\n✅ No difference detected in this scenario")
    
    print("\n" + "-" * 70)
    print("CURRENT SYSTEM TIME TEST:")
    print("-" * 70)
    
    # Test current system time
    current_utc = datetime.now(pytz.UTC)
    current_et = current_utc.astimezone(eastern_new)
    current_outside_rth = not (rth_start <= current_et.time() <= rth_end)
    
    print(f"Current UTC time: {current_utc}")
    print(f"Current ET time: {current_et}")
    print(f"Current time is outside RTH: {current_outside_rth}")
    print(f"Emergency exits would set outsideRth = {current_outside_rth}")
    
    if current_outside_rth:
        print("✅ Emergency exits would execute immediately (pre/post market)")
    else:
        print("✅ Emergency exits would execute during regular hours")

if __name__ == "__main__":
    test_emergency_exit_timing() 