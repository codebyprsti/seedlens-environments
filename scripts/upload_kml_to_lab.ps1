# Upload harvest KMLs + manifest from Windows to lab (run in PowerShell on your PC).
# Usage:
#   $env:LAB_SSH_PASS = "your-password"
#   .\scripts\upload_kml_to_lab.ps1 -SampleOnly
#   .\scripts\upload_kml_to_lab.ps1   # all 253

param(
    [string]$LabHost = "madanm@10.8.0.1",
    [string]$LabKml = "/home/madanm/kml_files",
    [string]$Src = "C:\Users\madan\Downloads\code-20260512T095809Z-3-001\code\planetscope-data-apis\kml\harvest_all_fields",
    [switch]$SampleOnly
)

$Plink = "C:\Program Files\PuTTY\plink.exe"
$Pscp = "C:\Program Files\PuTTY\pscp.exe"
if (-not (Test-Path $Plink)) { throw "Install PuTTY or use scp from Git Bash" }

$pw = $env:LAB_SSH_PASS
if (-not $pw) {
    Write-Host "Set: `$env:LAB_SSH_PASS = '...'" -ForegroundColor Yellow
    exit 2
}

function Invoke-Lab($cmd) {
    & $Plink -batch -pw $pw $LabHost $cmd
    if ($LASTEXITCODE -ne 0) { throw $cmd }
}

function Copy-Lab($local, $remote) {
    & $Pscp -batch -pw $pw $local "${LabHost}:${remote}"
    if ($LASTEXITCODE -ne 0) { throw "scp failed: $local" }
}

if (-not (Test-Path $Src)) { throw "Source not found: $Src" }

Invoke-Lab "mkdir -p $LabKml"

if ($SampleOnly) {
    Write-Host "Uploading sample IND-KA-600044 + manifest..."
    Copy-Lab "$Src\IND-KA-600044.kml" "$LabKml/"
    Copy-Lab "$Src\_harvest_all_manifest.csv" "$LabKml/"
} else {
    Write-Host "Uploading all KML + manifest (253 files)..."
    & $Pscp -batch -pw $pw "$Src\*.kml" "${LabHost}:${LabKml}/"
    Copy-Lab "$Src\_harvest_all_manifest.csv" "$LabKml/"
}

Invoke-Lab "find $LabKml -maxdepth 1 -name '*.kml' | wc -l; ls -la $LabKml/_harvest_all_manifest.csv"
Write-Host "Done. On lab run: ./scripts/lab_exec.sh validate --expected-kml 253" -ForegroundColor Green
