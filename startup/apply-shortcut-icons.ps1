$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$IconPath = Join-Path $ProjectRoot "favicon.ico"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$StartupShortcutPath = Join-Path $StartupFolder "ChickenSee.lnk"
$DesktopShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "ChickenSee Control Center.lnk"

if (-not (Test-Path $IconPath -PathType Leaf)) {
    Write-Warning "favicon.ico not found at: $IconPath"
    exit 0
}

$Shell = New-Object -ComObject WScript.Shell

if (Test-Path $DesktopShortcutPath) {
    $DesktopShortcut = $Shell.CreateShortcut($DesktopShortcutPath)
    $DesktopShortcut.IconLocation = "$IconPath,0"
    $DesktopShortcut.Save()
    Write-Host "Updated Desktop shortcut icon: $DesktopShortcutPath" -ForegroundColor Green
}

if (Test-Path $StartupShortcutPath) {
    $StartupShortcut = $Shell.CreateShortcut($StartupShortcutPath)
    $StartupShortcut.IconLocation = "$IconPath,0"
    $StartupShortcut.Save()
    Write-Host "Updated Startup shortcut icon: $StartupShortcutPath" -ForegroundColor Green
}
