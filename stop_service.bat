@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0utils\start_service.ps1" -Action stop %*
set "EMBER_LAUNCH_STATUS=%errorlevel%"
if not "%EMBER_LAUNCH_STATUS%"=="0" (
  echo.
  echo Ember service stop failed. See the message above.
  pause
)
exit /b %EMBER_LAUNCH_STATUS%
