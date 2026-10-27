$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Uninstaller = Join-Path $ProjectRoot "startup\uninstall-startup.ps1"
& $Uninstaller
if (-not $?) {
    throw "Failed to remove the ChickenSee startup launcher."
}
