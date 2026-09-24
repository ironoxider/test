@echo off
rem Windows: double-click this file to start Device Inventory from the source code.
rem First run sets up a private Python environment in .venv and installs what the app needs.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

set "FRESH="
if exist ".venv\Scripts\python.exe" goto install
echo First-time setup: installing what Device Inventory needs - about a minute...
%PY% -m venv .venv
if errorlevel 1 goto nopython
set "FRESH=1"

:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if not errorlevel 1 goto run
if defined FRESH goto installfailed
echo Could not check for updates to the add-ons - offline? Starting anyway...

:run
".venv\Scripts\python.exe" launcher.py
if errorlevel 1 pause
exit /b

:nopython
echo.
echo Python 3.9 or newer is needed but was not found.
echo Install it from https://www.python.org/downloads/ - on the first screen of the installer,
echo tick "Add python.exe to PATH". Then double-click this file again.
pause
exit /b 1

:installfailed
rmdir /s /q .venv
echo.
echo Installing the app's add-ons failed - see the messages above.
echo Check the internet connection and try again.
pause
exit /b 1
