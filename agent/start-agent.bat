@echo off
title ChickenSee Sync Agent
echo ========================================================
echo       Starting ChickenSee Sync Agent on Boot...
echo ========================================================
cd /d "%~dp0"
node watcher.js
pause
