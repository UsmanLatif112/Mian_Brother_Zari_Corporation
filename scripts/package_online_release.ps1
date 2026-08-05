# Build online host upload zip (no desktop exe)
# Usage: powershell -ExecutionPolicy Bypass -File scripts\package_online_release.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VersionPy = Get-Content (Join-Path $Root "app\version.py") -Raw
$Version = if ($VersionPy -match 'APP_VERSION\s*=\s*"([^"]+)"') { $Matches[1] } else { "1.0.0" }

$ReleaseDir = Join-Path $Root "releases"
$Stage = Join-Path $ReleaseDir "online-staging"
$ZipName = "AgriBooks-Online-$Version.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

Write-Host "==> Staging online package $Version ..." -ForegroundColor Cyan

$copyItems = @(
    "app",
    "config.py",
    "run.py",
    "requirements.txt",
    "passenger_wsgi.py",
    "ONLINE_DEPLOY.txt",
    ".env.online.example"
)
foreach ($item in $copyItems) {
    $src = Join-Path $Root $item
    if (-not (Test-Path $src)) {
        Write-Host "  skip missing: $item" -ForegroundColor Yellow
        continue
    }
    $dst = Join-Path $Stage $item
    if (Test-Path $src -PathType Container) {
        Copy-Item -Recurse -Force $src $dst
    } else {
        Copy-Item -Force $src $dst
    }
}

# Drop junk from app tree
Get-ChildItem -Path $Stage -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $Stage -Recurse -Include "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

# Do not ship local instance DB if present under app
$instance = Join-Path $Stage "instance"
if (Test-Path $instance) { Remove-Item -Recurse -Force $instance }

if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Online package ready:" -ForegroundColor Green
Write-Host "  $ZipPath"
Write-Host "Upload to your Python host, extract, configure .env from .env.online.example" -ForegroundColor Green
