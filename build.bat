@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set "PYCMD="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PYCMD=py -3"
)

if not defined PYCMD (
  for /f "delimiters=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do (
    if "!PYCMD!"=="" if exist "%%I" (
      "%%I" -c "import sys" >nul 2>&1
      if not errorlevel 1 set "PYCMD=%%I"
    )
  )
)

if not defined PYCMD (
  echo [ERROR] Python 3 not found. Install Python 3 or add it to PATH.
  pause
  exit /b 1
)

echo === ArkDownloader Build ===
echo Using: !PYCMD!
echo.

call !PYCMD! -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)

call !PYCMD! -m PyInstaller ArkDownloader.spec --noconfirm
if errorlevel 1 (
  echo [ERROR] PyInstaller build failed
  pause
  exit /b 1
)

if exist "dist\ArkDownloader.exe" (
  echo.
  echo Build OK: dist\ArkDownloader.exe
  explorer dist
) else (
  echo [ERROR] dist\ArkDownloader.exe not found
  pause
  exit /b 1
)

echo.
pause
endlocal
exit /b 0
