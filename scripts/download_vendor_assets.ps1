# Download front-end vendor assets for fully offline UI.
# Run from repo root when updating Bootstrap / Font Awesome / etc.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\download_vendor_assets.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Base = Join-Path $Root "app\static\vendor"

New-Item -ItemType Directory -Force -Path @(
  "$Base\bootstrap",
  "$Base\jquery",
  "$Base\datatables",
  "$Base\chartjs",
  "$Base\cropperjs",
  "$Base\fontawesome\css",
  "$Base\fontawesome\webfonts",
  "$Base\fonts"
) | Out-Null

function Get-File($Url, $Out) {
  Write-Host "→ $Url"
  Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing
}

Get-File "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" "$Base\bootstrap\bootstrap.min.css"
Get-File "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js" "$Base\bootstrap\bootstrap.bundle.min.js"
Get-File "https://code.jquery.com/jquery-3.7.1.min.js" "$Base\jquery\jquery.min.js"
Get-File "https://cdn.datatables.net/1.13.8/css/dataTables.bootstrap5.min.css" "$Base\datatables\dataTables.bootstrap5.min.css"
Get-File "https://cdn.datatables.net/1.13.8/js/jquery.dataTables.min.js" "$Base\datatables\jquery.dataTables.min.js"
Get-File "https://cdn.datatables.net/1.13.8/js/dataTables.bootstrap5.min.js" "$Base\datatables\dataTables.bootstrap5.min.js"
Get-File "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js" "$Base\chartjs\chart.umd.min.js"
Get-File "https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.css" "$Base\cropperjs\cropper.min.css"
Get-File "https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.js" "$Base\cropperjs\cropper.min.js"
Get-File "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css" "$Base\fontawesome\css\all.min.css"

foreach ($name in @(
  "fa-brands-400.woff2","fa-brands-400.ttf",
  "fa-regular-400.woff2","fa-regular-400.ttf",
  "fa-solid-900.woff2","fa-solid-900.ttf",
  "fa-v4compatibility.woff2","fa-v4compatibility.ttf"
)) {
  Get-File "https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/webfonts/$name" "$Base\fontawesome\webfonts\$name"
}

Get-File "https://fonts.gstatic.com/s/plusjakartasans/v12/LDIoaomQNQcsA88c7O9yZ4KMCoOg4Ko20yw.woff2" "$Base\fonts\plus-jakarta-sans-latin.woff2"
Get-File "https://fonts.gstatic.com/s/plusjakartasans/v12/LDIoaomQNQcsA88c7O9yZ4KMCoOg4Ko40yyygA.woff2" "$Base\fonts\plus-jakarta-sans-latin-ext.woff2"

Write-Host ""
Write-Host "Vendor assets ready under app\static\vendor" -ForegroundColor Green
Write-Host "Keep plus-jakarta-sans.css as the local font stylesheet."
