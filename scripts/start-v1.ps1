param([string]$Python = "python", [int]$Port = 8910, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
if ($Port -in @(5940, 8770)) { throw "This port belongs to v2. Use a separate v1 port (default: 8910)." }
$legacyRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "legacy\v1"
$venvPython = Join-Path $legacyRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython) -or -not (Test-Path (Join-Path $legacyRoot "frontend\dist\index.html"))) {
  & (Join-Path $PSScriptRoot "setup-v1.ps1") -Python $Python
}
$previousData = $env:ASTOCK_DATA_HOME
$previousMarket = $env:VR_DATA_DIR
$previousEncoding = $env:PYTHONUTF8
$server = $null
try {
  $env:ASTOCK_DATA_HOME = Join-Path $legacyRoot ".local\data\shortline"
  $env:VR_DATA_DIR = Join-Path $legacyRoot ".local\data\market"
  $env:PYTHONUTF8 = "1"
  $server = Start-Process -FilePath $venvPython -ArgumentList @("-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "$Port", "--no-access-log") -WorkingDirectory $legacyRoot -PassThru -NoNewWindow
  $url = "http://127.0.0.1:$Port"
  $ready = $false
  for ($attempt = 0; $attempt -lt 120; $attempt++) {
    $server.Refresh()
    if ($server.HasExited) { throw "v1 failed to start (exit code $($server.ExitCode))." }
    try { $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2; if ($response.StatusCode -eq 200) { $ready = $true; break } } catch {}
    Start-Sleep -Milliseconds 250
  }
  if (-not $ready) { throw "v1 did not become ready within 30 seconds." }
  Write-Host "Vibe AStock v1 is running at $url (v2 uses 5940/8770)."
  if (-not $NoBrowser) { Start-Process $url }
  Wait-Process -Id $server.Id
  $server.Refresh()
  $launchExit = $server.ExitCode
} finally {
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
  $env:ASTOCK_DATA_HOME = $previousData
  $env:VR_DATA_DIR = $previousMarket
  $env:PYTHONUTF8 = $previousEncoding
}
exit $launchExit
