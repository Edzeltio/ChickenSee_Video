#!/usr/bin/env bash
# ==============================================================================
# run-pipeline.sh - Linux launcher with auto-restart supervisor
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_DIR}" || exit 1

echo "=============================================================="
echo "   ChickenSee Edge AI Monitoring Daemon (Linux OptiPlex)"
echo "=============================================================="
echo "[START] Project directory: ${PROJECT_DIR}"
echo "[START] Active Hours: 06:00:00 to 18:00:00 daily"

while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting pipeline..."
    python3 -m edge.pipeline
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pipeline exited with code ${EXIT_CODE}. Restarting in 5s..."
    sleep 5
done
