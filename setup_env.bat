@echo off
setlocal
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    python "%~dp0setup_env.py" %*
    exit /b
)
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0setup_env.py" %*
    exit /b
)
echo Install Python 3.11 or newer, then open a new terminal and rerun setup. 1>&2
exit /b 1
