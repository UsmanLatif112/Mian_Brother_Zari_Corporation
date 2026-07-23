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

$Starter = Join-Path $Dist "START_HERE.bat"
@"
@echo off
title Mian Brother Fertilizers
cd /d "%~dp0"
echo Unblocking files (needed after downloading a zip)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"
echo Starting app...
start "" "%~dp0MianBrotherFertilizers.exe"
"@ | Set-Content -Path $Starter -Encoding ASCII

$Readme = Join-Path $Dist "HOW_TO_USE.txt"
@"
Mian Brother Fertilizers — Desktop App
======================================

IMPORTANT (if you downloaded this as a ZIP)
-------------------------------------------
Windows often blocks apps from zip downloads. Do ONE of these:

A) Easiest: double-click START_HERE.bat  (recommended)
B) Or: right-click the ZIP before extracting → Properties → check Unblock → OK,
   then extract and run MianBrotherFertilizers.exe

Normal use
----------
1. Keep this whole folder together (do not move only the .exe).
2. Prefer START_HERE.bat, or double-click MianBrotherFertilizers.exe.
3. First login: username admin  /  password admin123
   (Change the password after first login if you create more users.)
4. Your data is stored in the "data" folder next to the .exe
   (database, photos, backups). Copy that folder to move the shop data.
5. Needs Windows 10/11 with Microsoft Edge WebView2
   (already installed on most PCs).
   Install if missing:
   https://developer.microsoft.com/microsoft-edge/webview2/

To share with another user: zip this entire "MianBrotherFertilizers" folder
and send the zip — they unzip and run START_HERE.bat.
"@ | Set-Content -Path $Readme -Encoding UTF8

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Send this folder to users:" -ForegroundColor Green
Write-Host "  $Dist"
Write-Host ""
Write-Host "Tip: zip dist\MianBrotherFertilizers and share the zip." -ForegroundColor Yellow
Write-Host "Users should run START_HERE.bat after unzipping." -ForegroundColor Yellow
