@echo off

setlocal EnableExtensions

chcp 65001 >nul 2>&1

cd /d "%~dp0"



set "PYCMD="

set "LOGDIR=%APPDATA%\ArkDownloader"
if not exist "%LOGDIR%" (
  if exist "%APPDATA%\PanFetch" set "LOGDIR=%APPDATA%\PanFetch"
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

set "LOGFILE=%LOGDIR%\startup.log"

echo [%date% %time%] start.bat > "%LOGFILE%"



where py >nul 2>&1

if not errorlevel 1 (

  py -3 -c "import sys" >nul 2>&1

  if not errorlevel 1 set "PYCMD=py -3"

)



if not defined PYCMD (

  for /f "usebackq tokens=*" %%I in (`where python 2^>nul`) do (

    echo %%I | findstr /i "WindowsApps" >nul

    if errorlevel 1 (

      call :try_py "%%I"

      if defined PYCMD goto run

    )

  )

)



if not defined PYCMD call :try_py "D:\Program Files\Python313\python.exe"

if not defined PYCMD call :try_py "%LocalAppData%\Programs\Python\Python313\python.exe"

if not defined PYCMD call :try_py "%ProgramFiles%\Python313\python.exe"



if not defined PYCMD (

  echo [ERROR] Python 3 not found. >> "%LOGFILE%"

  echo [ERROR] Python 3 not found. Install from https://www.python.org/downloads/

  echo Log: %LOGFILE%

  pause

  exit /b 1

)



:run

echo Using: %PYCMD% >> "%LOGFILE%"

echo Starting ArkDownloader...

echo Using: %PYCMD%



call %PYCMD% main.py >> "%LOGFILE%" 2>&1

set "ERR=%ERRORLEVEL%"

echo Exit code: %ERR% >> "%LOGFILE%"



if not "%ERR%"=="0" (

  echo.

  echo [ERROR] ArkDownloader failed. See log:

  echo %LOGFILE%

  type "%LOGFILE%"

  echo.

  pause

  exit /b %ERR%

)



exit /b 0



:try_py

"%~1" -c "import sys" >nul 2>&1

if errorlevel 1 exit /b 1

set "PYCMD=%~1"

exit /b 0


