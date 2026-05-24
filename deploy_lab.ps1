# Bootstrap satellite deployment on Windows lab machine.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv ..."
    & $Py -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

pip install -q -r requirements.txt
$env:SATELLITE_PROJECT_ROOT = $Root
python -m satellite_deployment.bootstrap --install-deps --expected-kml $(if ($env:EXPECTED_KML) { $env:EXPECTED_KML } else { 253 })
Write-Host "Bootstrap complete. Next: copy .env.example to .env and set credentials"
Write-Host "  python scripts/run_satellite_lab.py migrate"
Write-Host "  .\run_pipeline.ps1 -Start 2025-12-01 -End 2026-03-18"
