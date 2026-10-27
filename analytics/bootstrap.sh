#!/usr/bin/env bash
# analytics/bootstrap.sh
# 1-Click Bootstrap Script for Linux / WSL / Ubuntu OptiPlex Server

set -e

echo "═══════════════════════════════════════════════════════════════"
echo "  ZAMPEN Native Layer Chicken Analytics Pipeline Bootstrap"
echo "  Dell OptiPlex Edge Hub (YOLO26 Nano + ByteTrack)"
echo "═══════════════════════════════════════════════════════════════"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 1. Check Python
python3 --version || { echo "Python 3 is required."; exit 1; }

# 2. Virtual Environment Setup
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "[STEP 1/4] Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi
source "$SCRIPT_DIR/venv/bin/activate"

# 3. Dependencies
echo "[STEP 2/4] Installing Python requirements..."
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# 4. Initialize Database
echo "[STEP 3/4] Initializing Database Schema..."
export PYTHONPATH="$PROJECT_ROOT"
python3 -c "from analytics.database import DB; DB.init_db(); print('[OK] Database initialized successfully.')"

# 5. Launch Services
echo "[STEP 4/4] Launching FastAPI and Worker Daemon..."
echo "  API Docs : http://localhost:8000/docs"
echo "  Health   : http://localhost:8000/health"
echo "  Latest   : http://localhost:8000/results/latest"

python3 -m analytics.api &
API_PID=$!

trap "echo 'Stopping API ($API_PID)...'; kill $API_PID; exit 0" SIGINT SIGTERM

python3 -m analytics.worker