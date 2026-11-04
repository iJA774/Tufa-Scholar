param([string]$PidFile)
if ([Console]::ReadLine() -ne 'start') { exit 1 }
$exe = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $exe)) { $exe = Join-Path $PSHOME 'pwsh.exe' }
$child = Start-Process -FilePath $exe -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 60') -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText($PidFile, [string]$child.Id)
