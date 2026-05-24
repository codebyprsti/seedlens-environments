# Run full harvest satellite ingestion (253 KMLs).
param(
    [string]$Start = "2025-12-01",
    [string]$End = "2026-03-18",
    [switch]$ResumeFailed,
    [string[]]$ExtraArgs
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:SATELLITE_PROJECT_ROOT = $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

$cmd = @(
    "scripts/run_satellite_lab.py", "run",
    "--start", $Start,
    "--end", $End,
    "--season-id", $(if ($env:SEASON_ID) { $env:SEASON_ID } else { "RABI_25_26" }),
    "--batch-strategy", $(if ($env:BATCH_STRATEGY) { $env:BATCH_STRATEGY } else { "quarterly" }),
    "--api-delay", $(if ($env:API_DELAY) { $env:API_DELAY } else { "2" })
)
if ($ResumeFailed) { $cmd += "--resume-failed" }
if ($ExtraArgs) { $cmd += $ExtraArgs }

python @cmd
