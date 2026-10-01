@echo off
rem Double-click to start Castle Tools and open it in the browser.
rem
rem The installer copies this file into the install folder
rem (%LOCALAPPDATA%\Programs\CastleTools) and points the Start-menu shortcut
rem at it. Everything it does is tools\desktop_launch.py under the installed
rem environment: a second launch reuses the running server, and it never
rem installs anything. Close this window to stop Castle Tools.
setlocal
set "ROOT=%CASTLE_TOOLS_HOME%"
if "%ROOT%"=="" if exist "%~dp0env\Scripts\python.exe" set "ROOT=%~dp0."
if "%ROOT%"=="" set "ROOT=%LOCALAPPDATA%\Programs\CastleTools"
set "PY=%ROOT%\env\Scripts\python.exe"
if not exist "%PY%" (
  echo Castle Tools are not installed in %ROOT%.
  echo Run installer\install.cmd from the Castle Tools folder first.
  pause
  exit /b 1
)
title Castle Tools
"%PY%" "%ROOT%\app\tools\desktop_launch.py" %*
if errorlevel 1 pause
