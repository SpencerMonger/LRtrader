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
LOG_FILE="$LOG_DIR/start_newstrader_$(date +\%Y\%m\%d).log"
EXEC_LOG="$LOG_DIR/script_execution.log"

# Log the execution
echo "=========================" >> "$EXEC_LOG"
echo "Script executed at $(date) [UTC]" >> "$EXEC_LOG"
echo "User: $(whoami)" >> "$EXEC_LOG"
echo "PWD: $(pwd)" >> "$EXEC_LOG"
echo "HOME: $HOME" >> "$EXEC_LOG"
echo "=========================" >> "$EXEC_LOG"

# Make sure the kill script is executable
chmod +x "$PROJECT_DIR/kill_newstrader.sh"

# Change to the project directory
cd "$PROJECT_DIR" || {
    echo "$(date) [UTC]: ERROR: Could not change to project directory $PROJECT_DIR" >> "$LOG_FILE"
    echo "ERROR: Could not change to project directory"
    exit 1
}
echo "Changed directory to $(pwd)" >> "$EXEC_LOG"

# Log Python path and version
PYTHON_BIN=$(which python3)
echo "Using Python: $PYTHON_BIN" >> "$EXEC_LOG"
$PYTHON_BIN --version >> "$EXEC_LOG" 2>&1

# Set PYTHONPATH
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"
echo "Set PYTHONPATH to include $PROJECT_DIR" >> "$EXEC_LOG"

# Check if screen is installed
if ! command -v screen &> /dev/null; then
    echo "$(date) [UTC]: ERROR: screen is not installed" >> "$LOG_FILE"
    echo "ERROR: screen is not installed. Please install it with: sudo apt-get install screen"
    exit 1
fi

# Kill existing screen session if it exists
if screen -ls | grep -q "newstrader"; then
    echo "$(date) [UTC]: newstrader screen session already exists, killing old one first" >> "$LOG_FILE"
    screen -X -S newstrader quit
    sleep 2
    
    # Double check if it was killed
    if screen -ls | grep -q "newstrader"; then
        echo "$(date) [UTC]: WARNING: Could not kill existing screen session" >> "$LOG_FILE"
        # Try more aggressively to kill the session
        screen_pid=$(screen -ls | grep newstrader | awk '{print $1}' | cut -d. -f1)
        if [ ! -z "$screen_pid" ]; then
            echo "$(date) [UTC]: Attempting to kill screen session with PID $screen_pid" >> "$LOG_FILE"
            kill -9 "$screen_pid"
            sleep 1
        fi
    fi
fi

# Additional check for any running Python processes related to the newstrader app
newstrader_pids=$(pgrep -f "python.*run_local\.py")
if [ ! -z "$newstrader_pids" ]; then
    echo "$(date) [UTC]: Found related Python processes. Attempting to terminate: $newstrader_pids" >> "$LOG_FILE"
    kill $newstrader_pids
    sleep 1
fi

# Run script in screen session using pdm
echo "$(date) [UTC]: Starting newstrader in a screen session" >> "$LOG_FILE"
echo "$(date) [UTC]: Python application will create its own logs in $LOG_DIR" >> "$LOG_FILE"
# The python script now handles its own logging, so we don't redirect stdout/stderr here.
COMMAND="/home/synk/.local/bin/pdm run python src/run_local.py --config global-news-signal-config.yaml --port 7496"
PORT=$(echo "$COMMAND" | grep -o '\--port [0-9]\+' | grep -o '[0-9]\+')
cd "$PROJECT_DIR" && screen -dmS newstrader $COMMAND
echo "Started screen session with name 'newstrader'" >> "$EXEC_LOG"

# Give it a moment to start
sleep 2

# Verify screen session is running
if screen -ls | grep -q "newstrader"; then
    echo "$(date) [UTC]: Confirmed newstrader screen session is running" >> "$LOG_FILE"
    echo "SUCCESS: newstrader screen session is running" >> "$LOG_FILE"
    echo "running on port $PORT"
else
    echo "$(date) [UTC]: ERROR: Failed to start newstrader screen session" >> "$LOG_FILE"
    echo "ERROR: Failed to start newstrader screen session" >> "$LOG_FILE"
    exit 1
fi

# List available screen sessions for reference
echo "Available screen sessions:" >> "$LOG_FILE"
screen -ls >> "$LOG_FILE"
