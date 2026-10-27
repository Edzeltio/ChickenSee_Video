$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Installer = Join-Path $ProjectRoot "startup\install-startup.ps1"
& $Installer
if (-not $?) {
    throw "Failed to install the ChickenSee startup launcher."
}
