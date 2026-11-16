@echo off
REM ==============================================================================
REM AI-SDLC MCP Environment & Launcher Script (Windows CMD)
REM Usage:
REM   run.bat           -> Sets environment variables in current CMD session
REM   run.bat jira      -> Sets env vars + runs ai_orchestrator.py
REM   run.bat github    -> Sets env vars + runs github_orchestrator.py
REM ==============================================================================

REM 1. API Keys & Tokens (Replace with your active credentials)
set "GOOGLE_API_KEY=your_googleapi_key"
set "JIRA_API_TOKEN=your_atlassian_jira_api_token"
set "GITHUB_TOKEN=your_github_personal_access_token"

REM 2. GitHub Repository Coordinates
set "GITHUB_OWNER=pavi550"
set "GITHUB_REPO=Capstone"
set "GITHUB_BRANCH=main"

REM 3. Python Windows Unbuffered & UTF-8 Flags
set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"

echo [OK] Environment variables set for Jira (CP) and GitHub (%GITHUB_OWNER%/%GITHUB_REPO%).

set "SCRIPT_DIR=%~dp0"

if /I "%~1"=="jira" (
    echo [RUN] Starting Jira Orchestrator ^(ai_orchestrator.py^)...
    python -u "%SCRIPT_DIR%ai_orchestrator.py"
) else if /I "%~1"=="github" (
    echo [RUN] Starting GitHub PR Orchestrator ^(github_orchestrator.py^)...
    python -u "%SCRIPT_DIR%github_orchestrator.py"
)
