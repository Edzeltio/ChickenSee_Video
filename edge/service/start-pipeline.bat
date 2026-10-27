@echo off
TITLE ChickenSee Edge AI Pipeline - OptiPlex Hub
cd /d "%~dp0\..\.."

echo ==============================================================
echo    ChickenSee Edge AI Monitoring Daemon (Dell OptiPlex)
echo ==============================================================
echo [START] Active Hours: 06:00:00 to 18:00:00 daily
echo [START] Model: YOLO26n (custom_poultry.pt) + ByteTrack
echo [START] Database: agent\data\chicken_telemetry.db
echo ==============================================================

:loop
echo [%date% %time%] Launching ChickenSee Edge Pipeline...
python -m edge.pipeline

echo [%date% %time%] Pipeline process exited with code %errorlevel%.
echo [%date% %time%] Auto-restarting in 5 seconds (Ctrl+C to terminate)...
timeout /t 5 /nobreak >nul
goto loop
