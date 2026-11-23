@echo off
setlocal EnableExtensions
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Scripts\Update_Index.ps1" -NoPreview || goto :failed

git lfs install --local || goto :failed

rem Store every XML file in Git LFS, regardless of its size.
git lfs track "*.xml" || goto :failed

rem Stage .gitattributes first so the LFS clean filter applies to XML files.
git add .gitattributes || goto :failed
git add . || goto :failed

git diff --cached --quiet
if errorlevel 1 git commit -m "Update Emmre agent tools library" || goto :failed

rem Convert XML blobs in local, unpushed commits to LFS pointers.
rem This repairs commits created before the LFS rule existed.
git lfs migrate import --include="*.xml" --yes || goto :failed

git lfs fsck || goto :failed
git lfs push --all origin || goto :failed
git push origin HEAD || goto :failed

echo SUCCESS: Pushed with XML files stored in Git LFS.
pause
exit /b 0

:failed
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
echo FAILED: Exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
