@echo off
setlocal
set "PYTHONUTF8=1"
if not exist "%~dp0..\.venv\Scripts\python.exe" (
  echo Install first: python -m venv .venv and pip install -e .
  pause
  exit /b 2
)
"%~dp0..\.venv\Scripts\python.exe" -m fabu client start
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
