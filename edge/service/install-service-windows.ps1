# ==============================================================================
# install-service-windows.ps1
# Registers ChickenSee Edge AI Pipeline as a persistent background Windows Task
# Starts automatically upon system boot with elevated permissions.
# ==============================================================================

$TaskName   = "ChickenSeeEdgePipeline"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = (Get-Item "$ScriptDir\..\..").FullName
$RunnerBat  = "$ScriptDir\start-pipeline.bat"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " ChickenSee Edge AI Service Installer (Windows OptiPlex)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Check if task already exists
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($ExistingTask) {
    Write-Host "[INFO] Existing task '$TaskName' detected. Unregistering first..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create Task Action & Trigger
$Action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$RunnerBat`"" -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew

# Register Task
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "ChickenSee Edge AI Vision & Sensor Telemetry Pipeline" `
        -RunLevel Highest

    Write-Host "`n[SUCCESS] '$TaskName' registered successfully!" -ForegroundColor Green
    Write-Host "          Triggers automatically at system startup."
    Write-Host "          To start immediately, run: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
} catch {
    Write-Host "`n[ERROR] Failed to register scheduled task: $_" -ForegroundColor Red
}
