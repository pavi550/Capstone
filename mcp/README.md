# AI-SDLC MCP Agents (Jira & GitHub — Gemini & OpenAI)

Two standalone AI-SDLC agents built on the **Model Context Protocol (MCP v2.x)** over `stdio`, supporting both **Google Gemini** and **OpenAI** (including automatic fallback when Gemini hits Free Tier rate limits):
- **Jira Agent (`ai_orchestrator.py` + `jira_mcp_server.py`)**: Automates backlog inspection, ticket creation, field updates, comments, and workflow transitions in Jira project **`CP`**.
- **GitHub PR Agent (`github_orchestrator.py` + `github_mcp_server.py`)**: Inspects repository files, searches code, creates feature branches, commits changes, and raises Pull Requests in **`pavi550/Capstone`**.

---

## 1. Folder Structure (`Capstone/mcp/`)

```text
Capstone/mcp/
├── jira_mcp_server.py       # Jira MCP Server (5 tools)
├── ai_orchestrator.py       # Standalone Jira orchestrator
├── github_mcp_server.py     # GitHub MCP Server (6 tools)
├── github_orchestrator.py   # Standalone GitHub PR orchestrator
├── llm_provider.py          # Shared LLM factory (Gemini, OpenAI, or Auto-Fallback)
├── run.sh                   # Environment & launcher script (Git Bash / MINGW64)
├── run.bat                  # Environment & launcher script (Windows CMD)
└── README.md                # Setup, configuration, and run guide
```

---

## 2. Prerequisites & Installation

Requires **Python 3.10+** (tested on Python 3.13 on Windows):

```bash
pip install "mcp>=2.0.0" langchain-core langchain-google-genai langchain-openai requests
```

---

## 3. Environment Variables & Credentials

Open `run.sh` (or `run.bat`) and replace the placeholder keys with your active credentials:

| Variable | Required For | What to Set | Where to Get It |
| :--- | :--- | :--- | :--- |
| `LLM_PROVIDER` | All runs | `gemini` (default), `openai`, or `auto` | Controls which LLM provider runs |
| `GOOGLE_API_KEY` | `gemini` or `auto` | Gemini API key (`AIza...`) | [Google AI Studio → API Keys](https://aistudio.google.com/apikey) |
| `OPENAI_API_KEY` | `openai` or `auto` | OpenAI API key (`sk-proj-...`) | [OpenAI Platform → API Keys](https://platform.openai.com/api-keys) |
| `JIRA_API_TOKEN` | `jira` runs | Atlassian API token for `pes2pge25ds030@stu.pes.edu` | [Atlassian Security → API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `GITHUB_TOKEN` | `github` runs | GitHub PAT (`Contents` & `Pull requests` Read/Write, or `repo` scope) | [GitHub → Developer Settings → PATs](https://github.com/settings/tokens) |
| `GITHUB_OWNER` | `github` runs | `pavi550` (default) | Repository owner |
| `GITHUB_REPO` | `github` runs | `Capstone` (default) | Repository name |
| `GITHUB_BRANCH` | `github` runs | `main` (default) | Base branch for Pull Requests |

*(Ensure `mcp/run.sh`, `mcp/run.bat`, and `mcp/*.log` are listed in `.gitignore` so API keys are never committed.)*

---

## 4. LLM Provider Modes (`LLM_PROVIDER`)

`llm_provider.py` routes tool-bound reasoning based on `LLM_PROVIDER`:
- **`gemini` (Default)**: Uses `gemini-3.8-flash` with automatic fallback to `gemini-3.8-pro`.
- **`openai`**: Uses `gpt-4o-mini` with automatic fallback to `gpt-4o`.
- **`auto`**: Starts with `gemini-3.8-flash` and automatically fails over to OpenAI `gpt-4o-mini` if Gemini hits a `429` rate limit or `503` spike.

---

## 5. How to Run (`run.sh` and `run.bat`)

> **Important:** Never run `jira_mcp_server.py` or `github_mcp_server.py` manually. Each orchestrator spawns its matching MCP server automatically over `stdio`, discovers its tools, executes the workflow, and terminates the child process cleanly.

### Option A: Git Bash (`run.sh`)
Always use `source` so exported environment variables remain active in your shell:

```bash
cd ~/Capstone/mcp

# Load environment variables only
source run.sh

# Run Jira Agent (defaults to Gemini)
source run.sh jira

# Run Jira Agent using OpenAI
source run.sh jira openai

# Run GitHub PR Agent using OpenAI
source run.sh github openai

# Run GitHub PR Agent with Gemini -> OpenAI Auto-Fallback
source run.sh github auto
```

### Option B: Windows Command Prompt (`run.bat`)
```cmd
cd C:\Users\mahes\Capstone\mcp

:: Set environment variables only
run.bat

:: Run Jira Agent (defaults to Gemini)
run.bat jira

:: Run Jira Agent using OpenAI
run.bat jira openai

:: Run GitHub PR Agent using OpenAI
run.bat github openai

:: Run GitHub PR Agent with Gemini -> OpenAI Auto-Fallback
run.bat github auto
```

---

## 6. How It Works

1. **Subprocess Handshake (`stdio`)**: The orchestrator spawns its target MCP server as a child Python process with `WindowsProactorEventLoopPolicy`, full environment inheritance (`env=os.environ.copy()`), and `stderr` diagnostic logging (`jira_server_err.log` / `github_server_err.log`).
2. **Dynamic Tool Discovery**: The client calls `session.list_tools()`, converts the MCP tool definitions into standard JSON function schemas, and binds them to the selected LLM (`Gemini` or `OpenAI`).
3. **Autonomous ReAct Loop**: The LLM inspects the prompt, executes Jira or GitHub tools iteratively, processes tool responses, and outputs a final summary report.

---

## 7. Available MCP Tools

### Jira Server (`jira_mcp_server.py` — Project `CP`)
| Tool | Purpose |
| :--- | :--- |
| `list_open_jiras` | Lists all open (`status != Done`) tickets with keys, issue types, and statuses. |
| `create_jira_ticket` | Creates a new ticket formatted in Atlassian Document Format (ADF). |
| `update_jira_fields` | Updates the summary and/or description of an existing ticket (e.g., `CP-2`). |
| `add_jira_comment` | Posts progress notes, logs, or PR URLs onto a ticket. |
| `update_jira_status` | Dynamically resolves workflow transition IDs and moves tickets (`To Do` → `In Progress` → `Done`). |

### GitHub Server (`github_mcp_server.py` — `pavi550/Capstone`)
| Tool | Purpose |
| :--- | :--- |
| `list_repo_files` | Lists files in the repo via GitHub's Git Trees API (supports path and extension filters). |
| `search_repo_code` | Searches across the codebase for classes, functions, or keywords. |
| `read_file` | Fetches and decodes file contents with line numbers before making edits. |
| `create_branch` | Cuts a new feature branch from `main` using the latest commit SHA. |
| `commit_file_change` | Creates or updates files on a feature branch (auto-resolves existing file SHAs and blocks direct commits to `main`). |
| `create_pull_request` | Opens a Pull Request into `main` and returns the direct GitHub PR URL. |
