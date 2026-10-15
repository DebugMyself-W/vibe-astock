param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$legacyRoot = Join-Path (Split-Path -Parent $PSScriptRoot) "legacy\v1"
$venvPython = Join-Path $legacyRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  & $Python -m venv (Join-Path $legacyRoot ".venv")
  if ($LASTEXITCODE -ne 0) { throw "Failed to create the v1 Python environment." }
}
& $venvPython -m pip install -r (Join-Path $legacyRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install v1 Python dependencies." }
# v1 uses only the mootdx TDX TCP client. Its obsolete httpx constraint conflicts with MCP.
& $venvPython -m pip install --no-deps -r (Join-Path $legacyRoot "requirements-market.txt")
if ($LASTEXITCODE -ne 0) { throw "Failed to install the v1 market adapter." }
& $venvPython -c "import fastapi,uvicorn,langgraph,langchain_openai,akshare,requests,dotenv,mcp,py_mini_racer,bs4,numpy,pandas,baostock,mootdx,rapidocr,onnxruntime,PIL"
if ($LASTEXITCODE -ne 0) { throw "The v1 Python import check failed." }
& npm.cmd ci --prefix (Join-Path $legacyRoot "frontend") --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw "Failed to install v1 frontend dependencies." }
& npm.cmd run build --prefix (Join-Path $legacyRoot "frontend")
if ($LASTEXITCODE -ne 0) { throw "Failed to build the v1 frontend." }
Write-Host "The isolated v1 environment is ready." -ForegroundColor Green
