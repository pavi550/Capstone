#!/usr/bin/env bash
# Options for LLM_PROVIDER: "gemini" | "openai" | "auto" (Gemini with OpenAI fallback)
export LLM_PROVIDER="${2:-gemini}"

export GOOGLE_API_KEY="AIzaSyYourActualGoogleAIStudioKey"
export OPENAI_API_KEY="openpai_key"
export JIRA_API_TOKEN="your_atlassian_jira_api_token"
export GITHUB_TOKEN="your_github_personal_access_token"

export GITHUB_OWNER="pavi550"
export GITHUB_REPO="Capstone"
export GITHUB_BRANCH="main"

export PYTHONUNBUFFERED="1"
export PYTHONUTF8="1"

echo "✅ Env loaded | Provider: ${LLM_PROVIDER} | Jira: CP | GitHub: ${GITHUB_OWNER}/${GITHUB_REPO}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$1" == "jira" ]]; then
    python -u "${SCRIPT_DIR}/ai_orchestrator.py"
elif [[ "$1" == "github" ]]; then
    python -u "${SCRIPT_DIR}/github_orchestrator.py"
fi
