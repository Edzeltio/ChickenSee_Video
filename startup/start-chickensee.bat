@echo off
setlocal

set "SUPERVISOR_SCRIPT=%~dp0run-chickensee.ps1"
if not exist "%SUPERVISOR_SCRIPT%" (
    echo ChickenSee startup supervisor not found: "%SUPERVISOR_SCRIPT%"
    exit /b 1
)

start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%SUPERVISOR_SCRIPT%"
exit /b 0
