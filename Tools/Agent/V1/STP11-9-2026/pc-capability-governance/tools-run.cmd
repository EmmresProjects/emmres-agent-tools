@echo off
REM Cross-platform launcher for the pc-capability-governance Skill Tool.
REM Finds a Python 3.9+ interpreter, then forwards every argument unchanged.
REM Usage:  tools-run.cmd self-test --evidence evidence
setlocal
set "HERE=%~dp0"
set "CMD=%HERE%tools-capability_governance.py"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%CMD%" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%CMD%" %*
  exit /b %ERRORLEVEL%
)

echo {"error":true,"status":"RUNTIME MISSING","message":"Python 3.9+ not found on PATH","exit_code":2}
exit /b 2
