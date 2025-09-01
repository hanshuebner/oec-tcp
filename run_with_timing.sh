#!/bin/bash

# Script to run OEC with timing logging enabled
# This will show detailed timing information for keystrokes and other operations

echo "Starting OEC with timing logging enabled..."
echo "Set OEC_LOG_LEVEL=DEBUG to see detailed timing information"
echo ""

# Set the logging level to DEBUG to see all timing information
export OEC_LOG_LEVEL=DEBUG

# Run the program with your usual arguments
# Modify the command below with your actual arguments
python -m oec "$@"

