@echo off
setlocal DisableDelayedExpansion
python -X utf8 -u "%~dp0launch.py" %*
exit /b %errorlevel%
