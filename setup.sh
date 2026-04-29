#!/bin/bash
# LaunchPad - First-time setup (macOS/Linux)

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "==> LaunchPad Setup"
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found. Please install Python 3.10 or later."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python $PYTHON_VERSION"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "==> Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Install dependencies
echo "==> Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt

# Install Playwright browsers
echo "==> Installing Playwright Chromium (this may take a minute)..."
python -m playwright install chromium

# Copy .env if missing
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "==> Created .env from template"
fi

# Create directories
mkdir -p users logs

echo ""
echo "==> Setup complete!"
echo ""
echo "To start LaunchPad, run:"
echo "   ./start.sh"
echo ""
