@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\Update_Index.ps1" -NoPreview || goto :failed
git lfs install --local || goto :failed
git add . || goto :failed
git diff --cached --quiet
if errorlevel 1 git commit -m "Update Emmre agent tools library" || goto :failed
git push || goto :failed
echo SUCCESS: Pushed.
pause
exit /b 0
:failed
echo FAILED: Exit code %ERRORLEVEL%.
pause
exit /b %ERRORLEVEL%
