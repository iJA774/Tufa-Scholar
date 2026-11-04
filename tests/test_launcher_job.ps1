$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $root 'scripts/launcher-job.ps1')
$exe = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $exe)) { $exe = Join-Path $PSHOME 'pwsh.exe' }
$pidFile = [IO.Path]::GetTempFileName()
$job = [IntPtr]::Zero
$sentinel = $null
$descendant = $null
$wrapper = $null
try {
    $sentinel = Start-Process -FilePath $exe -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru
    $job = [TufaLauncherJob]::Create()
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $exe
    $fixture = Join-Path $PSScriptRoot 'launcher_wrapper.ps1'
    $info.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$fixture`" -PidFile `"$pidFile`""
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $wrapper = [Diagnostics.Process]::Start($info)
    [TufaLauncherJob]::Assign($job, $wrapper.Handle)
    $wrapper.StandardInput.WriteLine('start')
    $wrapper.StandardInput.Close()
    if (-not $wrapper.WaitForExit(10000) -or $wrapper.ExitCode -ne 0) { throw 'Wrapper failed' }
    $childId = [int][IO.File]::ReadAllText($pidFile)
    $descendant = Get-Process -Id $childId -ErrorAction Stop
    if ($descendant.HasExited) { throw 'Descendant exited before job closed' }
    [void][TufaLauncherJob]::CloseHandle($job)
    $job = [IntPtr]::Zero
    if (-not $descendant.WaitForExit(10000)) { throw 'Job descendant survived job close' }
    $sentinel.Refresh()
    if ($sentinel.HasExited) { throw 'Unrelated sentinel was terminated' }
    Write-Host 'Job lifecycle test passed'
} finally {
    if ($job -ne [IntPtr]::Zero) { [void][TufaLauncherJob]::CloseHandle($job) }
    if ($wrapper -and -not $wrapper.HasExited) { $wrapper.Kill() }
    if ($descendant -and -not $descendant.HasExited) { $descendant.Kill() }
    if ($sentinel -and -not $sentinel.HasExited) { $sentinel.Kill() }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
