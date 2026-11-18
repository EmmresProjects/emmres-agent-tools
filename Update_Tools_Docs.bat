@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Emmre Tools Documentation Updater

set "LOGDIR=%~dp0logs"
set "LOGFILE=%LOGDIR%\Update_Tools_Docs.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

> "%LOGFILE%" echo ============================================================
>>"%LOGFILE%" echo Emmre Tools Documentation Updater
>>"%LOGFILE%" echo Started: %DATE% %TIME%
>>"%LOGFILE%" echo Repository: %CD%
>>"%LOGFILE%" echo ============================================================

echo ============================================================
echo Emmre Tools Documentation Updater
echo Started: %DATE% %TIME%
echo Repository: %CD%
echo Log: %LOGFILE%
echo ============================================================
echo.

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: Windows PowerShell was not found.
  >>"%LOGFILE%" echo ERROR: Windows PowerShell was not found.
  set "RESULT=1"
  goto :finish
)

echo Running documentation update...
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\Update_Tools_Docs.ps1" 2>&1 | "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -Command "$input | ForEach-Object { $_; Add-Content -LiteralPath $env:LOGFILE -Value $_ -Encoding UTF8 }"
set "RESULT=%ERRORLEVEL%"

:finish
>>"%LOGFILE%" echo.
>>"%LOGFILE%" echo Finished: %DATE% %TIME%
>>"%LOGFILE%" echo Exit code: %RESULT%

echo.
echo ============================================================
if "%RESULT%"=="0" (
  echo SUCCESS: Tools documentation updated.
) else (
  echo FAILED: Documentation update returned exit code %RESULT%.
)
echo Log saved to:
echo %LOGFILE%
echo ============================================================
echo.
pause
exit /b %RESULT%
