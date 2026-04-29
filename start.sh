#!/bin/bash
# LaunchPad - Start server (macOS/Linux)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Error: .venv not found. Run ./setup.sh first."
    exit 1
fi

source .venv/bin/activate

# Load .env
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

PORT=${LAUNCHPAD_PORT:-7070}
HOST=${LAUNCHPAD_HOST:-0.0.0.0}

python -m uvicorn server:app --host "$HOST" --port "$PORT" --reload
