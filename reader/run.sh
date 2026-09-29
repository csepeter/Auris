#!/usr/bin/env bash
# Auris - Linux / macOS launcher
# Usage: bash run.sh

set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "The Auris virtual environment is missing: reader/.venv"
    echo "Run: bash reader/setup.sh"
    exit 1
fi
PYTHON=".venv/bin/python"

echo "Starting Auris..."
echo "Open your browser at: http://127.0.0.1:7860"
echo ""
echo "Model status will appear in the top-right corner of the app."
echo "(Model loads in the background; you can browse and read while it loads.)"
echo ""
echo "Press Ctrl+C to stop."

"$PYTHON" app.py
