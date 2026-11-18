@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Emmre One-Click Update and Push

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\One_Click_Update_And_Push.ps1"
set "RESULT=%ERRORLEVEL%"

echo.
if "%RESULT%"=="0" (
  echo SUCCESS: Documentation, Git commit, LFS upload, and push completed.
) else (
  echo FAILED: Exit code %RESULT%. Review the log shown above.
)
echo.
pause
exit /b %RESULT%
