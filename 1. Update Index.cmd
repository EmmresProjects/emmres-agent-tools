@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Update Emmre Agent Tools Index
set "PY="
where py.exe >nul 2>nul && set "PY=py.exe -3"
if not defined PY where python.exe >nul 2>nul && set "PY=python.exe"
if not defined PY (
  echo FAILED: Python 3 was not found. Install Python or run this from Emmre's Local Environment.
  pause
  exit /b 1
)
%PY% "%~dp0Scripts\build_index.py"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (echo SUCCESS: Index and catalog regenerated.) else (echo FAILED: Index generation returned %RC%.)
pause
exit /b %RC%
