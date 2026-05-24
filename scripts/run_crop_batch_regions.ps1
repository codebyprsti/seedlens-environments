# Two-phase batch: locations_only (Google + field_locations) then crop_indices for each S3 prefix.
# Full transcript: Tee-Object -> log file (prints + stderr/logging).

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
# UTF-8 lines (avoid Windows PowerShell Tee-Object default UTF-16)
$log = Join-Path $logDir "crop_batch_regions_2026-03-22_utf8.log"

$base = "seedworks/kml_files/input_files"
$prefixes = @(
    "$base/Odisha/BHADRAK/",
    "$base/Telanagna/Telanagna/",
    "$base/Karnataka/",
    "$base/OneDrive_1_3-12-2026/CG/",
    "$base/OneDrive_1_3-12-2026/Karnataka/",
    "$base/OneDrive_1_3-12-2026/Odisha/",
    "$base/OneDrive_1_3-12-2026/telangana/"
)

function Write-LogLine {
    param([string]$Line)
    Write-Host $Line
    Add-Content -Path $log -Value $Line -Encoding UTF8
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine ""
Write-LogLine "========== REGION BATCH START $stamp =========="

foreach ($p in $prefixes) {
    Write-LogLine ""
    Write-LogLine "---------- PREFIX: $p ----------"

    Write-LogLine "Phase 1: locations_only"
    & python scripts/run_crop_analysis_s3_batch.py --prefix $p --mode locations_only 2>&1 | ForEach-Object { Write-LogLine $_ }

    Write-LogLine "Phase 2: crop_indices"
    & python scripts/run_crop_analysis_s3_batch.py --prefix $p 2>&1 | ForEach-Object { Write-LogLine $_ }
}

$end = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine ""
Write-LogLine "========== REGION BATCH END $end =========="
Write-Host "Done. Log: $log"
