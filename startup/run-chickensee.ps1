$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AgentDir = Join-Path $ProjectRoot "agent"
$Python = (Get-Command python -ErrorAction Stop).Source
$Node = (Get-Command node -ErrorAction Stop).Source
$Weights = Join-Path $ProjectRoot "custom_poultry.pt"
$AgentDependencies = Join-Path $AgentDir "node_modules"

if (-not (Test-Path $Weights -PathType Leaf)) {
    throw "Custom model weights not found: $Weights"
}
if (-not (Test-Path $AgentDependencies -PathType Container)) {
    throw "Sync-agent dependencies are missing. Run 'npm ci' in $AgentDir first."
}

$StorageRoot = $null
$AgentEnv = Join-Path $AgentDir ".env"
if (Test-Path $AgentEnv -PathType Leaf) {
    foreach ($Line in Get-Content $AgentEnv) {
        if ($Line -match '^\s*CHICKENSEE_LOCAL_STORAGE\s*=\s*(.*?)\s*$') {
            $StorageRoot = $Matches[1].Trim('"').Trim("'")
            break
        }
    }
}
$FallbackStorage = Join-Path $ProjectRoot "local-storage"
$LogDir = $null
if ($StorageRoot) {
    try {
        $TestLogDir = Join-Path $StorageRoot "logs"
        New-Item -Path $TestLogDir -ItemType Directory -Force -ErrorAction Stop | Out-Null
        $LogDir = $TestLogDir
    } catch {
        $StorageRoot = $FallbackStorage
    }
}
if (-not $LogDir) {
    $StorageRoot = $FallbackStorage
    $LogDir = Join-Path $StorageRoot "logs"
    New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
}
$SupervisorLog = Join-Path $LogDir "startup-supervisor.log"
$StopRequest = Join-Path $AgentDir ".stop-request"
$LegacyStopRequest = Join-Path $LogDir "stop-request"
$SupervisorState = Join-Path $AgentDir ".supervisor-state.json"
$Mutex = New-Object System.Threading.Mutex($false, "Local\ChickenSeeStartupSupervisor")
if (-not $Mutex.WaitOne(0, $false)) {
    Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] Another ChickenSee supervisor is already running."
    exit 0
}

$Services = @(
    @{
        Name = "sync-agent"
        Executable = $Node
        Arguments = @("watcher.js")
        WorkingDirectory = $AgentDir
    },
    @{
        Name = "analytics-api"
        Executable = $Python
        Arguments = @("-u", "-m", "analytics.api")
        WorkingDirectory = $ProjectRoot
    },
    @{
        Name = "analytics-worker"
        Executable = $Python
        Arguments = @("-u", "-m", "analytics.worker")
        WorkingDirectory = $ProjectRoot
    }
)
$RunningServices = @{}

function Start-ChickenSeeService {
    param([hashtable]$Service)

    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $StdoutLog = Join-Path $LogDir "$($Service.Name)-$Timestamp-out.log"
    $StderrLog = Join-Path $LogDir "$($Service.Name)-$Timestamp-error.log"
    $Process = Start-Process `
        -FilePath $Service.Executable `
        -ArgumentList $Service.Arguments `
        -WorkingDirectory $Service.WorkingDirectory `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -WindowStyle Hidden `
        -PassThru

    $RunningServices[$Service.Name] = $Process
    Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] Started $($Service.Name), PID $($Process.Id). Logs: $StdoutLog"
}

function Get-CandidateProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe' or Name = 'python.exe'" -ErrorAction SilentlyContinue)
}

function Find-ExistingServiceProcess {
    param(
        [hashtable]$Service,
        [array]$CandidateProcesses
    )

    $ProcessName = if ($Service.Name -eq "sync-agent") {
        [System.IO.Path]::GetFileName($Node)
    } else {
        [System.IO.Path]::GetFileName($Python)
    }
    $CommandPattern = switch ($Service.Name) {
        "sync-agent" { '(?i)\bwatcher\.js\b' }
        "analytics-api" { '(?i)\s-m\s+analytics\.api(?:\s|$)' }
        "analytics-worker" { '(?i)\s-m\s+analytics\.worker(?:\s|$)' }
    }
    $Matches = @(
        $CandidateProcesses |
            Where-Object {
                $_.Name -ieq $ProcessName -and $_.CommandLine -match $CommandPattern
            }
    )
    if ($Matches.Count -gt 1) {
        $Ids = ($Matches | ForEach-Object { $_.ProcessId }) -join ", "
        throw "Multiple $($Service.Name) processes already exist (PIDs $Ids). Stop duplicates before starting ChickenSee."
    }
    if ($Matches.Count -eq 1) {
        return Get-Process -Id $Matches[0].ProcessId -ErrorAction Stop
    }
    return $null
}

try {
    @{
        process_id = $PID
        storage_root = $StorageRoot
        started_at = (Get-Date -Format o)
    } | ConvertTo-Json | Set-Content -Path $SupervisorState -Encoding UTF8
    if (Test-Path $StopRequest) {
        Remove-Item -Path $StopRequest -Force
    }
    if (Test-Path $LegacyStopRequest) {
        Remove-Item -Path $LegacyStopRequest -Force
    }
    Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] ChickenSee startup supervisor started."

    $CandidateProcesses = Get-CandidateProcesses
    $ExistingProcesses = @{}
    foreach ($Service in $Services) {
        $ExistingProcesses[$Service.Name] = Find-ExistingServiceProcess -Service $Service -CandidateProcesses $CandidateProcesses
    }

    foreach ($Service in $Services) {
        $ExistingProcess = $ExistingProcesses[$Service.Name]
        if ($ExistingProcess) {
            $RunningServices[$Service.Name] = $ExistingProcess
            Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] Adopting existing $($Service.Name), PID $($ExistingProcess.Id)."
        } else {
            Start-ChickenSeeService $Service
        }
    }

    while ($true) {
        for ($second = 0; $second -lt 10; $second++) {
            Start-Sleep -Seconds 1
            if ((Test-Path $StopRequest) -or (Test-Path $LegacyStopRequest)) {
                break
            }
        }
        if ((Test-Path $StopRequest) -or (Test-Path $LegacyStopRequest)) {
            Remove-Item -Path $StopRequest -Force -ErrorAction SilentlyContinue
            Remove-Item -Path $LegacyStopRequest -Force -ErrorAction SilentlyContinue
            Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] Stop requested from ChickenSee Control Center."
            break
        }
        foreach ($Service in $Services) {
            $Process = $RunningServices[$Service.Name]
            if ($Process.HasExited) {
                Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] $($Service.Name) exited with code $($Process.ExitCode); restarting in 5 seconds."
                Start-Sleep -Seconds 5
                Start-ChickenSeeService $Service
            }
        }
    }
}
finally {
    foreach ($Process in $RunningServices.Values) {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -Path $SupervisorState -Force -ErrorAction SilentlyContinue
    Add-Content -Path $SupervisorLog -Value "[$(Get-Date -Format o)] ChickenSee startup supervisor stopped."
    $Mutex.ReleaseMutex()
    $Mutex.Dispose()
}
