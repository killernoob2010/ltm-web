@echo off
setlocal

call "%~dp0collector\installer\build_windows.cmd" %*
exit /b %ERRORLEVEL%
