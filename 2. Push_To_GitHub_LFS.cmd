@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Emmre Git and LFS Push

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\Git_LFS_Push.ps1"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
  echo SUCCESS: Git and LFS push completed.
) else (
  echo FAILED or CANCELLED: Exit code %RESULT%.
)
echo.
pause
exit /b %RESULT%
