@echo off
rem Double-click to install Castle Tools on Windows. Runs install.ps1 with
rem this process's execution policy relaxed (no machine setting changes),
rem passing every argument through: install.cmd --repair, --update,
rem --uninstall [--purge], --dry-run, --no-shortcut, --from-source.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
