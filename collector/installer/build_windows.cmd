@echo off
setlocal

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo 未找到 Windows PowerShell，无法启动构建脚本。
    if not defined WH6_NO_PAUSE pause
    exit /b 1
)

set "PS_ARGS="
if defined WH6_NO_PAUSE set "PS_ARGS=-NoPause"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %PS_ARGS% %*
set "EXIT_CODE=%ERRORLEVEL%"
if not defined WH6_NO_PAUSE pause
exit /b %EXIT_CODE%
