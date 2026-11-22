@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\Update_Index.ps1"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (echo SUCCESS: Index regenerated.) else (echo FAILED: Index generation returned %RC%.)
pause
exit /b %RC%
