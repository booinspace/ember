@echo off
powershell.exe -NoLogo -NoProfile -File "%~dp0scripts\select_deployment.ps1"
set "EMBER_EXIT=%ERRORLEVEL%"
if not "%EMBER_EXIT%"=="0" pause
exit /b %EMBER_EXIT%
