# ==============================================================================
# uninstall-service-windows.ps1
# Unregisters the ChickenSee Edge AI Scheduled Task from Windows.
# ==============================================================================

$TaskName = "ChickenSeeEdgePipeline"

Write-Host "Unregistering Scheduled Task: $TaskName..." -ForegroundColor Yellow

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($ExistingTask) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[SUCCESS] Task '$TaskName' has been removed." -ForegroundColor Green
} else {
    Write-Host "[INFO] Task '$TaskName' was not found on this system." -ForegroundColor Gray
}
