# Start the local API, one worker, and web development server in this window.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$children = @()
$previousApiUrl = $env:TUFA_API_URL
$exitCode = 0

try {
    $uv = (Get-Command uv.exe -CommandType Application -ErrorAction Stop).Source
    $pnpm = (Get-Command pnpm.cmd -CommandType Application -ErrorAction Stop).Source

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($port in @(8000, 3000)) {
        if ($listeners | Where-Object { $_.Port -eq $port }) {
            throw "Port $port is already in use. Stop the existing service before starting Tufa Scholar."
        }
    }

    $logDir = Join-Path $root ('.cache/start/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $env:TUFA_API_URL = 'http://127.0.0.1:8000'
    Write-Host "Logs: $logDir"
    Write-Host 'Starting Tufa Scholar. Press Ctrl+C to stop all three processes.'

    $children += Start-Process -FilePath $uv -ArgumentList @('run', '--locked', '--no-sync', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'api.out.log') -RedirectStandardError (Join-Path $logDir 'api.err.log') -PassThru
    $children += Start-Process -FilePath $uv -ArgumentList @('run', '--locked', '--no-sync', 'python', '-m', 'apps.worker') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'worker.out.log') -RedirectStandardError (Join-Path $logDir 'worker.err.log') -PassThru
    $children += Start-Process -FilePath $env:ComSpec -ArgumentList @('/d', '/c', "`"$pnpm`" dev:web") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'web.out.log') -RedirectStandardError (Join-Path $logDir 'web.err.log') -PassThru

    Write-Host 'Web: http://127.0.0.1:3000  API: http://127.0.0.1:8000'
    while ($true) {
        foreach ($child in $children) {
            $child.Refresh()
            if ($child.HasExited) {
                throw "A service exited (PID $($child.Id), exit code $($child.ExitCode)). See logs in $logDir."
            }
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Error -ErrorRecord $_ -ErrorAction Continue
    $exitCode = 1
} finally {
    foreach ($child in $children) {
        $child.Refresh()
        if (-not $child.HasExited) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $child.Id /T /F 2>$null | Out-Null
        }
    }
    $env:TUFA_API_URL = $previousApiUrl
    if ($children.Count -gt 0) {
        Write-Host 'Tufa Scholar stopped.'
    }
}
exit $exitCode
