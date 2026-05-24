# Harvest lab deploy: upload 1 sample KML + manifest, validate mapping, run single-field ingest.
# Usage (do NOT commit password to git):
#   $env:LAB_SSH_PASS = "your-password"
#   .\scripts\lab_deploy_sample.ps1

$ErrorActionPreference = "Stop"
$LabHost = "madanm@10.8.0.1"
$LabKml = "/home/madanm/kml_files"
$LabRepo = "/home/madanm/SeedIQ-Prod"
$SampleId = "IND-KA-600044"
$Src = "C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields"
$Plink = "C:\Program Files\PuTTY\plink.exe"
$Pscp = "C:\Program Files\PuTTY\pscp.exe"

if (-not $env:LAB_SSH_PASS) {
    Write-Host "Set password: `$env:LAB_SSH_PASS = '...'" -ForegroundColor Yellow
    exit 2
}
$pw = $env:LAB_SSH_PASS

function Invoke-Lab([string]$Cmd) {
    & $Plink -batch -pw $pw $LabHost $Cmd
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed: $Cmd" }
}

function Copy-Lab([string]$Local, [string]$Remote) {
    & $Pscp -batch -pw $pw $Local "${LabHost}:${Remote}"
    if ($LASTEXITCODE -ne 0) { throw "SCP failed: $Local" }
}

Write-Host "=== 1. Test SSH ===" -ForegroundColor Cyan
Invoke-Lab "hostname && whoami"

Write-Host "=== 2. Prepare kml dir ===" -ForegroundColor Cyan
Invoke-Lab "mkdir -p $LabKml"

Write-Host "=== 3. Upload sample + manifest ===" -ForegroundColor Cyan
Copy-Lab "$Src\$SampleId.kml" "$LabKml/"
Copy-Lab "$Src\_harvest_all_manifest.csv" "$LabKml/"
Invoke-Lab "ls -la $LabKml/"

Write-Host "=== 4. Validate mapping ===" -ForegroundColor Cyan
$validate = @"
cd $LabRepo && source venv/bin/activate && \
export SATELLITE_KML_DIR=$LabKml && export SATELLITE_MAPPING_DIR=$LabKml && \
python scripts/lab_validate_harvest_mapping.py \
  --kml-dir $LabKml --mapping-dir $LabKml \
  --only-internal-id $SampleId --resolve-growers --log-level WARNING
"@
Invoke-Lab $validate

Write-Host "=== 5. Start single-field ingestion (background) ===" -ForegroundColor Cyan
$run = @"
cd $LabRepo && source venv/bin/activate && \
export SATELLITE_KML_DIR=$LabKml && export SATELLITE_MAPPING_DIR=$LabKml && \
mkdir -p logs/satellite && \
nohup python scripts/run_satellite_lab.py run \
  --start 2025-12-01 --end 2026-03-18 --season-id RABI_25_26 \
  --only-internal-id $SampleId \
  > logs/satellite/single_field_test.log 2>&1 & echo PID=\$!
"@
Invoke-Lab $run

Write-Host "=== Done. Tail log on lab: ===" -ForegroundColor Green
Write-Host "  ssh $LabHost"
Write-Host "  tail -f $LabRepo/logs/satellite/single_field_test.log"
