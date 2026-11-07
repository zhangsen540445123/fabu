@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Fixed desktop profile: do not inherit a stale shell's FABU_* identity or tool paths.
if not defined LOCALAPPDATA goto missing_localappdata
rem The launcher reads the explicit physical store from start-fabu.profile.json.
set "FABU_STATE_DIR="
set "FABU_PORT=8765"
set "FABU_BIND_HOST=127.0.0.1"
set "FABU_DATA_DIR="
set "FABU_TOOLS_DIR="
set "FABU_NODE_PATH="
set "FABU_TEST_MODE="
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" goto missing_python
rem Use the same authenticated launcher as PowerShell, without PowerShell policy changes.
"%PYTHON%" -I -X utf8 -m fabu.launcher start --project-root "%~dp0.."
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
echo.
echo Fabu start failed. Read the error above; no process was force-killed.
pause
exit /b %RESULT%

:missing_python
echo Python environment missing: "%PYTHON%"
echo Follow the project README to install the local environment.
pause
exit /b 1

:missing_localappdata
echo LOCALAPPDATA is missing. Run as your normal Windows user.
pause
exit /b 1
