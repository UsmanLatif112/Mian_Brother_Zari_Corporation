@echo off
title Mian Brother Fertilizers
cd /d "%~dp0"
echo Unblocking files (needed after downloading a zip)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"
echo Starting app...
start "" "%~dp0MianBrotherFertilizers.exe"
