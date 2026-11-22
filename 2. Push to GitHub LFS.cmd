@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Push Emmre Agent Tools to GitHub LFS
call "1. Update Index.cmd"
if errorlevel 1 exit /b %ERRORLEVEL%
where git.exe >nul 2>nul || (echo FAILED: Git is not available.& pause& exit /b 1)
git rev-parse --is-inside-work-tree >nul 2>nul || (echo FAILED: Initialize this folder as a Git repository and add a remote first.& pause& exit /b 1)
git lfs install --local || goto :failed
git add . || goto :failed
git diff --cached --quiet
if errorlevel 1 git commit -m "Update Emmre agent tools library" || goto :failed
git push || goto :failed
echo SUCCESS: Repository and Git LFS files pushed.
pause
exit /b 0
:failed
echo FAILED: Git or Git LFS returned exit code %ERRORLEVEL%.
pause
exit /b %ERRORLEVEL%
