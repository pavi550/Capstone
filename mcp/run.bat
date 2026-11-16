@echo off
if "%~2"=="" (
    set "LLM_PROVIDER=gemini"
) else (
    set "LLM_PROVIDER=%~2"
)

set "GOOGLE_API_KEY=AIzaSyYourActualGoogleAIStudioKey"
set "OPENAI_API_KEY=sk-proj-YourActualOpenAIKey"
set "JIRA_API_TOKEN=your_atlassian_jira_api_token"
set "GITHUB_TOKEN=your_github_personal_access_token"

set "GITHUB_OWNER=pavi550"
set "GITHUB_REPO=Capstone"
set "GITHUB_BRANCH=main"

set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"

echo [OK] Env set ^| Provider: %LLM_PROVIDER% ^| Jira: CP ^| GitHub: %GITHUB_OWNER%/%GITHUB_REPO%

set "SCRIPT_DIR=%~dp0"

if /I "%~1"=="jira" (
    python -u "%SCRIPT_DIR%ai_orchestrator.py"
) else if /I "%~1"=="github" (
    python -u "%SCRIPT_DIR%github_orchestrator.py"
)
