#!/usr/bin/env bash
# ==============================================================================
# AI-SDLC MCP Environment & Launcher Script (Git Bash / MINGW64)
# Usage:
#   source run.sh           -> Loads environment variables into current shell
#   source run.sh jira      -> Loads env vars + runs ai_orchestrator.py
#   source run.sh github    -> Loads env vars + runs github_orchestrator.py
# ==============================================================================

# 1. API Keys & Tokens (Replace with your active credentials)
export GOOGLE_API_KEY="your_google_Api_key"
export JIRA_API_TOKEN="your_atlassian_jira_api_token"
export GITHUB_TOKEN="your_github_personal_access_token"

# 2. GitHub Repository Coordinates
export GITHUB_OWNER="pavi550"
export GITHUB_REPO="Capstone"
export GITHUB_BRANCH="main"

# 3. Python Windows Unbuffered & UTF-8 Flags
export PYTHONUNBUFFERED="1"
export PYTHONUTF8="1"

echo "✅ Environment variables exported for Jira (CP) and GitHub (${GITHUB_OWNER}/${GITHUB_REPO})."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$1" == "jira" ]]; then
    echo "🚀 Starting Jira Orchestrator (ai_orchestrator.py)..."
    python -u "${SCRIPT_DIR}/ai_orchestrator.py"
elif [[ "$1" == "github" ]]; then
    echo "🚀 Starting GitHub PR Orchestrator (github_orchestrator.py)..."
    python -u "${SCRIPT_DIR}/github_orchestrator.py"
fi
