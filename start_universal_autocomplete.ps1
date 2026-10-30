# Launches the Universal Autocomplete app (overlay + control center).
# Completions come from an API provider (Typhoon, Ollama, LM Studio, OpenAI, Claude, Gemini ...),
# picked in the app's Provider row or in ua_config.json.
$ErrorActionPreference = "Continue"
$AppScript = Join-Path $PSScriptRoot "universal_autocomplete.py"

# Python: $env:UA_PYTHON if set, else the py launcher, else python on PATH
$PythonExe = $env:UA_PYTHON
if (-not $PythonExe) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { $PythonExe = $py.Source } else { $PythonExe = (Get-Command python).Source }
}

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host " Universal Windows Autocomplete" -ForegroundColor Green
Write-Host " Works in every program: Notepad, Chrome, Edge, Word, LINE, IDEs" -ForegroundColor Yellow
Write-Host "==================================================================" -ForegroundColor Cyan

$ConfigPath = Join-Path $PSScriptRoot "ua_config.json"
$Provider = $env:UA_PROVIDER
if (-not $Provider -and (Test-Path $ConfigPath)) {
    try { $Provider = (Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).provider } catch {}
}
if (-not $Provider) { $Provider = "typhoon" }
Write-Host "[i] Provider: $Provider  (change it in the app's Provider row)" -ForegroundColor Gray

Write-Host "[>] Launching Universal Autocomplete..." -ForegroundColor Green
& $PythonExe $AppScript
