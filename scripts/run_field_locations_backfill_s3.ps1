<#
.SYNOPSIS
  Backfill operations.field_locations from S3 KML prefixes (Google geocode only when centroid missing).

.DESCRIPTION
  Runs: python scripts/run_crop_analysis_s3_batch.py --mode locations_only --prefix <prefix>
  One log file per prefix under logs/field_locations_backfill/

  Requires: .env with DB + GOOGLE_API_KEY (or GOOGLE_MAPS_API_KEY), AWS/boto for S3 + Secrets Manager.

.EXAMPLE
  pwsh -File scripts/run_field_locations_backfill_s3.ps1
  pwsh -File scripts/run_field_locations_backfill_s3.ps1 -GeocodeDelay 0.45
#>
param(
    [double]$GeocodeDelay = 0.35
)

$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs\field_locations_backfill"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$MasterLog = Join-Path $LogDir "MASTER_$Stamp.log"

function Write-Master {
    param([string]$Line)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $Line" | Out-File -FilePath $MasterLog -Append -Encoding utf8
}

$prefixes = @(
    @{ Tag = "Odisha_BHADRAK";    Prefix = "seedworks/kml_files/input_files/Odisha/BHADRAK/" },
    @{ Tag = "Telanagna_Telanagna"; Prefix = "seedworks/kml_files/input_files/Telanagna/Telanagna/" },
    @{ Tag = "Karnataka";         Prefix = "seedworks/kml_files/input_files/Karnataka/" },
    @{ Tag = "OneDrive_CG";       Prefix = "seedworks/kml_files/input_files/OneDrive_1_3-12-2026/CG/" },
    @{ Tag = "OneDrive_Karnataka"; Prefix = "seedworks/kml_files/input_files/OneDrive_1_3-12-2026/Karnataka/" },
    @{ Tag = "OneDrive_Odisha";   Prefix = "seedworks/kml_files/input_files/OneDrive_1_3-12-2026/Odisha/" },
    @{ Tag = "OneDrive_telangana"; Prefix = "seedworks/kml_files/input_files/OneDrive_1_3-12-2026/telangana/" }
)

Write-Master "Repo: $RepoRoot"
Write-Master "Mode: locations_only (field_locations backfill + Google when missing centroid)"
Write-Master "Geocode delay: $GeocodeDelay"
Write-Master "Prefixes: $($prefixes.Count)"

foreach ($item in $prefixes) {
    $tag = $item.Tag
    $prefix = $item.Prefix
    $outLog = Join-Path $LogDir "backfill_${tag}_$Stamp.log"
    Write-Master "START $tag prefix=$prefix log=$outLog"

    $argPrefix = $prefix.Replace('"', '""')
    $cmd = "cd /d `"$RepoRoot`" && set PYTHONIOENCODING=utf-8&& set PYTHONUTF8=1&& python scripts\run_crop_analysis_s3_batch.py --mode locations_only --prefix `"$argPrefix`" --geocode-delay $GeocodeDelay > `"$outLog`" 2>&1"
    cmd.exe /c $cmd
    $code = $LASTEXITCODE
    Write-Master "END $tag exit_code=$code log=$outLog"
}

Write-Master "ALL DONE"
Write-Host "Master log: $MasterLog"
Write-Host "Per-prefix logs in: $LogDir"
