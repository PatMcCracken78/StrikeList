@echo off
setlocal
rem Windows launcher: double-click. First run sets up a private Python environment (~1 min).
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto deps

rem ---- find Python 3 (py launcher first, then python / python3 on PATH)
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  python3 --version >nul 2>&1
  if not errorlevel 1 set "PY=python3"
)
if not defined PY goto nopython

echo First run: setting up Strike List with %PY%, this takes about a minute...
%PY% -m venv .venv
if errorlevel 1 goto venvfail
".venv\Scripts\python.exe" -m pip install -q --upgrade pip

:deps
rem ---- (re)install packages whenever requirements.txt changes (e.g. after an update)
fc /b requirements.txt ".venv\req-installed.txt" >nul 2>&1
if not errorlevel 1 goto run
echo Installing/updating components...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto pipfail
copy /y requirements.txt ".venv\req-installed.txt" >nul

:run
start "" ".venv\Scripts\pythonw.exe" strikelist.py
exit /b 0

:nopython
echo Strike List could not find Python 3.
echo Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during install), then run this again.
pause
exit /b 1

:venvfail
echo Could not create the Python environment in "%cd%\.venv".
echo Try moving the StrikeList folder somewhere you own, e.g. Documents, and run this again.
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1

:pipfail
echo Installing components failed - check your internet connection and run this again.
pause
exit /b 1
