param([Parameter(Mandatory = $true)][ValidateSet('api', 'worker', 'web')][string]$Role)
$ErrorActionPreference = 'Stop'
# If the launcher exits before assigning this helper to its job, EOF prevents startup.
if ([Console]::ReadLine() -ne 'start') { exit 1 }
$root = Split-Path -Parent $PSScriptRoot
$logDir = $env:TUFA_LAUNCH_LOG_DIR
try {
    if ($Role -eq 'api') {
        $exe = (Get-Command uv.exe -CommandType Application -ErrorAction Stop).Source
        $arguments = @('run', '--locked', '--no-sync', 'uvicorn', 'apps.api.main:app', '--host', '127.0.0.1', '--port', '8000')
    } elseif ($Role -eq 'worker') {
        $exe = (Get-Command uv.exe -CommandType Application -ErrorAction Stop).Source
        $arguments = @('run', '--locked', '--no-sync', 'python', '-m', 'apps.worker')
    } else {
        $pnpm = (Get-Command pnpm.cmd -CommandType Application -ErrorAction Stop).Source
        $exe = $env:ComSpec
        $arguments = @('/d', '/c', "`"$pnpm`" dev:web")
    }
    $process = Start-Process -FilePath $exe -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$Role.out.log") -RedirectStandardError (Join-Path $logDir "$Role.err.log") -PassThru -Wait
    exit $process.ExitCode
} catch {
    $_ | Out-String | Add-Content -Path (Join-Path $logDir "$Role.err.log")
    exit 1
}
