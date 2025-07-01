#!/bin/bash

# Define home and project directories with absolute paths
HOME="/home/synk"
PROJECT_DIR="$HOME/Development/newstrader"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Clean up old start/kill script logs (older than 5 days)
find "$LOG_DIR" -name "start_newstrader_*.log" -type f -mtime +5 -delete
find "$LOG_DIR" -name "kill_newstrader_*.log" -type f -mtime +5 -delete

# Log file for today
LOG_FILE="$LOG_DIR/kill_newstrader_$(date +\%Y\%m\%d).log"

echo "=========================" >> "$LOG_FILE"
echo "Kill script executed at $(date) [UTC]" >> "$LOG_FILE"
echo "User: $(whoami)" >> "$LOG_FILE"
echo "PWD: $(pwd)" >> "$LOG_FILE"
echo "=========================" >> "$LOG_FILE"

echo "$(date) [UTC]: Attempting graceful shutdown of newstrader application" >> "$LOG_FILE"

# First send SIGINT (Ctrl+C) to the process running in the screen session
if screen -ls | grep -q "newstrader"; then
    echo "$(date) [UTC]: Sending Ctrl+C signal to gracefully terminate the process" >> "$LOG_FILE"
    screen -S newstrader -X stuff '^C'

    echo "$(date) [UTC]: Waiting 10 minutes for newstrader to safely unwind positions..." >> "$LOG_FILE"
    echo "Graceful shutdown initiated. Waiting 10 minutes for positions to unwind..."
    
    # Wait 10 minutes for the process to clean up
    sleep 600
    
    echo "$(date) [UTC]: Grace period complete, now terminating screen session" >> "$LOG_FILE"
    
    # Now terminate the screen session
    screen -X -S newstrader quit

    # Give it a moment to terminate
    sleep 2

    # Double check
    if screen -ls | grep -q "newstrader"; then
        echo "$(date) [UTC]: WARNING: Screen session could not be killed after quit command" >> "$LOG_FILE"
        # Try more aggressively to kill the session
        screen_pid=$(screen -ls | grep newstrader | awk '{print $1}' | cut -d. -f1)
        if [ ! -z "$screen_pid" ]; then
            echo "$(date) [UTC]: Attempting to kill screen session with PID $screen_pid" >> "$LOG_FILE"
            kill -9 "$screen_pid"
            sleep 1
        fi
        
        # Check again after forced kill
        if screen -ls | grep -q "newstrader"; then
            echo "$(date) [UTC]: ERROR: Still could not kill the screen session" >> "$LOG_FILE"
            echo "ERROR: Failed to terminate newstrader screen session" >> "$LOG_FILE"
        else
            echo "$(date) [UTC]: Successfully terminated screen session after forced kill" >> "$LOG_FILE"
            echo "SUCCESS: Terminated screen session after forced kill" >> "$LOG_FILE"
        fi
    else
        echo "$(date) [UTC]: Successfully terminated screen session" >> "$LOG_FILE"
        echo "SUCCESS: Terminated screen session" >> "$LOG_FILE"
    fi
fi

# Additional check for any running Python processes related to the newstrader app
newstrader_pids=$(pgrep -f "python.*run_local\.py")
if [ ! -z "$newstrader_pids" ]; then
    echo "$(date) [UTC]: Found related Python processes. Attempting to terminate: $newstrader_pids" >> "$LOG_FILE"
    kill $newstrader_pids
    sleep 1
    
    # Check if processes are still running
    remaining_pids=$(pgrep -f "python.*run_local\.py")
    if [ ! -z "$remaining_pids" ]; then
        echo "$(date) [UTC]: WARNING: Some processes still running. Attempting force kill: $remaining_pids" >> "$LOG_FILE"
        kill -9 $remaining_pids
        echo "WARNING: Had to force kill some Python processes" >> "$LOG_FILE"
    else
        echo "$(date) [UTC]: Successfully terminated all Python processes" >> "$LOG_FILE"
        echo "SUCCESS: All Python processes terminated" >> "$LOG_FILE"
    fi
else
    echo "$(date) [UTC]: No related Python processes found" >> "$LOG_FILE"
    echo "INFO: No related Python processes were running" >> "$LOG_FILE"
fi

# List available screen sessions for reference
echo "Current screen sessions:" >> "$LOG_FILE"
screen -ls >> "$LOG_FILE"
