$ErrorActionPreference = "Stop"

$StartupFolder = [Environment]::GetFolderPath("Startup")
$Shortcuts = @(
    (Join-Path $StartupFolder "ChickenSee.lnk"),
    (Join-Path $StartupFolder "ChickenSee Control Center.lnk"),
    (Join-Path $StartupFolder "ChickenSeeAgent.lnk")
)

foreach ($Shortcut in $Shortcuts) {
    if (Test-Path $Shortcut) {
        Remove-Item -Path $Shortcut -Force
        Write-Host "Removed startup shortcut: $Shortcut"
    }
}

try {
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (Get-ItemProperty -Path $RunKey -Name "ChickenSee" -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $RunKey -Name "ChickenSee" -Force -ErrorAction SilentlyContinue
        Write-Host "Removed ChickenSee logon Run registry entry."
    }
} catch {
    # Ignore registry cleanup errors
}

Write-Host "ChickenSee will no longer start automatically at sign-in."
Write-Host "The ChickenSee Control Center desktop shortcut is left in place."
Write-Host "Already-running services are not stopped by this command."
