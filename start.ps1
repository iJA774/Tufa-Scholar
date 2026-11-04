param([switch]$Dev)

# Start the local API, one worker, and web server in this window.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$children = @()
$previousApiUrl = $env:TUFA_API_URL
$previousLogDir = $env:TUFA_LAUNCH_LOG_DIR
$job = [IntPtr]::Zero
$exitCode = 0

. (Join-Path $root 'scripts/launcher-job.ps1')

try {
    $null = Get-Command uv.exe -CommandType Application -ErrorAction Stop
    $null = Get-Command pnpm.cmd -CommandType Application -ErrorAction Stop
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($port in @(8000, 3000)) {
        if ($listeners | Where-Object { $_.Port -eq $port }) {
            throw "Port $port is already in use. Stop the existing service before starting Tufa Scholar."
        }
    }

    $logDir = Join-Path $root ('.cache/start/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $env:TUFA_API_URL = 'http://127.0.0.1:8000'
    $env:TUFA_LAUNCH_LOG_DIR = $logDir
    $job = [TufaLauncherJob]::Create()
    Write-Host "Logs: $logDir"
    $mode = if ($Dev) { 'development' } else { 'production' }
    Write-Host "Starting Tufa Scholar ($mode). Press Ctrl+C to stop."

    # Helpers wait for input until assigned; their service descendants inherit the job.
    $helper = Join-Path $root 'scripts/launcher-child.ps1'
    function Start-OwnedChild([string]$role) {
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $PSHOME 'powershell.exe'
        if (-not (Test-Path -LiteralPath $info.FileName)) {
            $info.FileName = Join-Path $PSHOME 'pwsh.exe'
        }
        $info.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$helper`" $role"
        $info.WorkingDirectory = $root
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $child = [System.Diagnostics.Process]::Start($info)
        try {
            [TufaLauncherJob]::Assign($job, $child.Handle)
        } catch {
            $child.Kill()
            throw
        }
        $child.StandardInput.WriteLine('start')
        $child.StandardInput.Close()
        return $child
    }

    if (-not $Dev) {
        Write-Host 'Building the web application (see build logs for details)...'
        $build = Start-OwnedChild 'build'
        $children += $build
        while (-not $build.HasExited) {
            Start-Sleep -Seconds 1
            $build.Refresh()
        }
        if ($build.ExitCode -ne 0) {
            throw "Web build failed (exit code $($build.ExitCode)). See logs in $logDir."
        }
        $children = @()
    }
    $webRole = if ($Dev) { 'web-dev' } else { 'web' }
    foreach ($role in @('api', 'worker', $webRole)) {
        $children += Start-OwnedChild $role
    }
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
    if ($job -ne [IntPtr]::Zero) {
        [void][TufaLauncherJob]::CloseHandle($job)
    }
    $env:TUFA_API_URL = $previousApiUrl
    $env:TUFA_LAUNCH_LOG_DIR = $previousLogDir
    if ($children.Count -gt 0) {
        Write-Host 'Tufa Scholar stopped.'
    }
}
exit $exitCode
