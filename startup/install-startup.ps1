$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunnerPath = Join-Path $PSScriptRoot "run-chickensee.ps1"
$LauncherPath = Join-Path $PSScriptRoot "start-chickensee.bat"
$DesktopAppPath = Join-Path $PSScriptRoot "chickensee_desktop.py"
$AgentDir = Join-Path $ProjectRoot "agent"
$Python = (Get-Command python -ErrorAction Stop).Source
$Node = (Get-Command node -ErrorAction Stop).Source
$PythonWindowed = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
$StartupFolder = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $StartupFolder "ChickenSee.lnk"
$DesktopShortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "ChickenSee Control Center.lnk"

if (-not (Test-Path $RunnerPath -PathType Leaf)) {
    throw "Startup supervisor not found: $RunnerPath"
}
if (-not (Test-Path $LauncherPath -PathType Leaf)) {
    throw "Startup batch launcher not found: $LauncherPath"
}
if (-not (Test-Path $DesktopAppPath -PathType Leaf)) {
    throw "ChickenSee desktop app not found: $DesktopAppPath"
}
if (-not (Test-Path $PythonWindowed -PathType Leaf)) {
    throw "Python's windowed launcher was not found: $PythonWindowed"
}
if (-not (Test-Path (Join-Path $ProjectRoot "custom_poultry.pt") -PathType Leaf)) {
    throw "Custom model weights are missing from the repository root."
}
if (-not (Test-Path (Join-Path $AgentDir "node_modules") -PathType Container)) {
    throw "Sync-agent packages are missing. Run 'npm ci' from $AgentDir first."
}
$DirectoriesFile = Join-Path $AgentDir "directories.json"
if (-not (Test-Path $DirectoriesFile -PathType Leaf)) {
    throw "Watched folders are not configured. Open ChickenSee Control Center and add video folders first."
}
$DirectoryConfig = Get-Content $DirectoriesFile -Raw | ConvertFrom-Json
$WatchedDirectories = @($DirectoryConfig.directories)
$ValidDirectories = @($WatchedDirectories | Where-Object { Test-Path $_ -PathType Container })
if ($ValidDirectories.Count -eq 0) {
    $DefaultData = Join-Path $AgentDir "data"
    New-Item -Path $DefaultData -ItemType Directory -Force | Out-Null
    $ValidDirectories = @($DefaultData)
}
& $Python -c "import cv2, fastapi, psutil, sqlalchemy, tkinter, ultralytics, uvicorn"
if ($LASTEXITCODE -ne 0) {
    throw "Analytics packages are missing. Run 'python -m pip install -r analytics\requirements.txt' from $ProjectRoot."
}
& $Node --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Node.js could not be started: $Node"
}
Push-Location $AgentDir
try {
    & $Node --input-type=module -e "import('chokidar'); import('@vercel/blob')"
    $NodePackageExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($NodePackageExitCode -ne 0) {
    throw "Sync-agent packages could not be loaded. Run 'npm ci' from $AgentDir."
}

$IconPath = Join-Path $ProjectRoot "favicon.ico"

$DesktopAppStartupShortcut = Join-Path $StartupFolder "ChickenSee Control Center.lnk"

$Shell = New-Object -ComObject WScript.Shell
$CommandPrompt = (Get-Command cmd.exe -ErrorAction Stop).Source

# 1. Background Supervisor Startup Shortcut
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $CommandPrompt
$Shortcut.Arguments = "/c `"`"$LauncherPath`"`""
$Shortcut.WorkingDirectory = $ProjectRoot
$Shortcut.WindowStyle = 7
$Shortcut.Description = "Start ChickenSee sync agent and analytics services at sign-in"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Save()

# 2. Control Center Desktop Shortcut
$DesktopShortcut = $Shell.CreateShortcut($DesktopShortcutPath)
$DesktopShortcut.TargetPath = $PythonWindowed
$DesktopShortcut.Arguments = "`"$DesktopAppPath`""
$DesktopShortcut.WorkingDirectory = $ProjectRoot
$DesktopShortcut.Description = "ChickenSee local analysis and sync control center"
if (Test-Path $IconPath) {
    $DesktopShortcut.IconLocation = "$IconPath,0"
}
$DesktopShortcut.Save()

# 3. Control Center Startup Shortcut (Launches desktop GUI on boot/login)
$AppStartup = $Shell.CreateShortcut($DesktopAppStartupShortcut)
$AppStartup.TargetPath = $PythonWindowed
$AppStartup.Arguments = "`"$DesktopAppPath`""
$AppStartup.WorkingDirectory = $ProjectRoot
$AppStartup.Description = "ChickenSee Control Center GUI launcher at sign-in"
if (Test-Path $IconPath) {
    $AppStartup.IconLocation = "$IconPath,0"
}
$AppStartup.Save()

# 4. Eliminate Windows Explorer 10-30s Startup Delay
try {
    $SerializeKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize"
    if (-not (Test-Path $SerializeKey)) {
        New-Item -Path $SerializeKey -Force | Out-Null
    }
    Set-ItemProperty -Path $SerializeKey -Name "StartupDelayInMSec" -Value 0 -Type DWord -Force
    Write-Host "Windows startup delay set to 0ms (instant real-time startup enabled)." -ForegroundColor Cyan
} catch {
    Write-Host "Could not configure StartupDelayInMSec in registry: $($_.Exception.Message)" -ForegroundColor Yellow
}

# 5. Register instant Run key in Windows Registry for immediate session startup
try {
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $RunCommand = "`"$CommandPrompt`" /c `"`"$LauncherPath`"`""
    Set-ItemProperty -Path $RunKey -Name "ChickenSee" -Value $RunCommand -Type String -Force
    Write-Host "Real-time logon registry entry configured in HKCU Run key." -ForegroundColor Cyan
} catch {
    Write-Host "Could not write Run key in registry: $($_.Exception.Message)" -ForegroundColor Yellow
}

$LegacyShortcut = Join-Path $StartupFolder "ChickenSeeAgent.lnk"
if (Test-Path $LegacyShortcut) {
    Remove-Item -Path $LegacyShortcut -Force
}

Write-Host "ChickenSee real-time startup installed successfully!" -ForegroundColor Green
Write-Host "Startup supervisor shortcut : $ShortcutPath"
Write-Host "Startup control center shortcut: $DesktopAppStartupShortcut"
Write-Host "Desktop app shortcut         : $DesktopShortcutPath"
Write-Host "ChickenSee will launch immediately without delay when the OptiPlex starts up." -ForegroundColor Green
