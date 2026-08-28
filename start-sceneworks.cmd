@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-sceneworks.ps1" %*
if errorlevel 1 (
  echo.
  echo SceneWorks failed to start. Review the error above.
  pause
)
