param([string]$Until = '2026-10-14T12:00:00+09:00')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$deadline = [DateTimeOffset]::Parse($Until)
if ($deadline -le [DateTimeOffset]::Now) { throw 'Until must be in the future.' }
$pointerPath = Join-Path $projectRoot 'local-data/a08/current-continuous-session.txt'
if (Test-Path -LiteralPath $pointerPath) {
    $previousDirectory = (Get-Content -LiteralPath $pointerPath -Raw).Trim()
    $previousStatusPath = Join-Path $previousDirectory 'status.json'
    if (Test-Path -LiteralPath $previousStatusPath) {
        $previousStatus = Get-Content -LiteralPath $previousStatusPath -Raw | ConvertFrom-Json
        if ($previousStatus.state -notin @('complete', 'failed')) {
            $previousProcess = Get-Process -Id $previousStatus.pid -ErrorAction SilentlyContinue
            if ($previousProcess) { throw "Recorder already running: $previousDirectory" }
        }
    }
}
$sessionDirectory = Join-Path $projectRoot ('local-data/a08/continuous-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$pythonRuntime = 'C:/ncs/toolchains/4f5b6ad6dd/opt/bin/python.exe'
$recordProcess = Start-Process -FilePath $pythonRuntime -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -ArgumentList @('-u','tools/a08_capture/continuous_uart.py','--output',('"' + $sessionDirectory + '"'),'--until',$Until) -RedirectStandardOutput "$sessionDirectory.stdout.log" -RedirectStandardError "$sessionDirectory.stderr.log"
Set-Content -LiteralPath $pointerPath -Value $sessionDirectory
Write-Output "Recorder PID: $($recordProcess.Id)"
Write-Output "Output: $sessionDirectory"
