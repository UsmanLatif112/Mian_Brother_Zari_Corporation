# Build distributable desktop app (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "==> Installing desktop build packages..." -ForegroundColor Cyan
python -m pip install -q -r requirements.txt
python -m pip install -q pywebview waitress pyinstaller pillow

Write-Host "==> Creating app icon..." -ForegroundColor Cyan
python scripts\make_app_icon.py

Write-Host "==> Building with PyInstaller (this can take several minutes)..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean MianBrotherFertilizers.spec

$Dist = Join-Path $Root "dist\MianBrotherFertilizers"
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

# Ship MySQL / sync connection settings with the desktop package
$DataDir = Join-Path $Dist "data"
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$EnvSrc = Join-Path $Root ".env"
$EnvDst = Join-Path $DataDir ".env"
if (Test-Path $EnvSrc) {
    Copy-Item -Force $EnvSrc $EnvDst
    Write-Host "==> Included data/.env (MySQL sync connection)" -ForegroundColor Cyan
} else {
    Write-Host "==> WARNING: .env not found - Sync will show MySQL Offline" -ForegroundColor Yellow
}

$Starter = Join-Path $Dist "START_HERE.bat"
$starterLines = @(
    "@echo off",
    "title Mian Brother Fertilizers",
    "cd /d `"%~dp0`"",
    "echo Unblocking files (needed after downloading a zip)...",
    "powershell -NoProfile -ExecutionPolicy Bypass -Command `"Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue`"",
    "echo Starting app...",
    "start `"`" `"%~dp0MianBrotherFertilizers.exe`""
)
$starterLines | Set-Content -Path $Starter -Encoding ASCII

$Readme = Join-Path $Dist "HOW_TO_USE.txt"
$readmeText = @"
Mian Brother Fertilizers - Desktop App
======================================

IMPORTANT (if you downloaded this as a ZIP)
-------------------------------------------
Windows often blocks apps from zip downloads. Do ONE of these:

A) Easiest: double-click START_HERE.bat  (recommended)
B) Or: right-click the ZIP before extracting -> Properties -> check Unblock -> OK,
   then extract and run MianBrotherFertilizers.exe

Normal use
----------
1. Keep this whole folder together (do not move only the .exe).
2. Prefer START_HERE.bat, or double-click MianBrotherFertilizers.exe.
3. First login: username admin  /  password admin123
   (Change the password after first login if you create more users.)
4. Your data is stored in the "data" folder next to the .exe
   (database, photos, backups, and data/.env for MySQL sync).
5. Works fully offline (no internet needed for normal use).
   Needs Windows 10/11 with Microsoft Edge WebView2
   (already installed on most PCs).
   Install if missing:
   https://developer.microsoft.com/microsoft-edge/webview2/
6. Sync and Backup: MySQL connection is in data/.env.
   When internet can reach the MySQL server, status shows Online
   and you can Push local data to MySQL.

To share with another user: zip this entire "MianBrotherFertilizers" folder
and send the zip - they unzip and run START_HERE.bat.
"@
Set-Content -Path $Readme -Value $readmeText -Encoding UTF8

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Send this folder to users:" -ForegroundColor Green
Write-Host "  $Dist"
Write-Host ""
Write-Host "Tip: zip dist\MianBrotherFertilizers and share the zip." -ForegroundColor Yellow
Write-Host "Users should run START_HERE.bat after unzipping." -ForegroundColor Yellow
