@echo off
setlocal

set "BUILDER=%~dp0collector\installer\build_windows.cmd"
if not exist "%BUILDER%" (
    echo [失败] 未找到 WH6 构建脚本：%BUILDER%
    echo 请把本文件与 collector 文件夹放在同一目录后重试。
    if not defined WH6_NO_PAUSE pause
    exit /b 1
)

call "%BUILDER%" %*
exit /b %ERRORLEVEL%
