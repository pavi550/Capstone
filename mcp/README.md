# AI-SDLC MCP Agents (Jira & GitHub)

Two standalone Gemini-powered AI agents built using the **Model Context Protocol (MCP v2.x)** over `stdio`:
- **Jira Agent (`ai_orchestrator.py` + `jira_mcp_server.py`)**: Automates backlog tracking, ticket creation, updates, comments, and workflow status changes in Jira project **`CP`**.
- **GitHub PR Agent (`github_orchestrator.py` + `github_mcp_server.py`)**: Reads repository code, creates feature branches, commits changes, and opens Pull Requests in **`pavi550/Capstone`**.

---

## 1. Folder Structure (`Capstone/mcp/`)

```text
Capstone/mcp/
├── jira_mcp_server.py       # Jira MCP Server (5 tools)
├── ai_orchestrator.py       # Gemini client for Jira
├── github_mcp_server.py     # GitHub MCP Server (6 tools)
├── github_orchestrator.py   # Gemini client for GitHub
├── run.sh                   # Environment & launcher script (Git Bash)
├── run.bat                  # Environment & launcher script (Windows CMD)
└── README.md                # This guide
```

---

## 2. What You Need to Set Up

### A. Install Python Dependencies
Requires Python 3.10+ (tested on Python 3.13):
```bash
pip install "mcp>=2.0.0" langchain-core langchain-google-genai requests
```

### B. Required Environment Variables
Open `run.sh` (or `run.bat`) and replace the placeholder values with your active credentials:

| Variable | What to Set | Where to Get It |
| :--- | :--- | :--- |
| `GOOGLE_API_KEY` | Gemini API key (`AIza...`) | [Google AI Studio → API Keys](https://aistudio.google.com/apikey) |
| `JIRA_API_TOKEN` | Atlassian API token for `pes2pge25ds030@stu.pes.edu` | [Atlassian Security → API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `GITHUB_TOKEN` | GitHub PAT with `Contents` & `Pull requests` Read/Write (or `repo` scope) | [GitHub → Developer Settings → PATs](https://github.com/settings/tokens) |
| `GITHUB_OWNER` | `pavi550` (default) | Repository owner |
| `GITHUB_REPO` | `Capstone` (default) | Repository name |
| `GITHUB_BRANCH` | `main` (default) | Base branch for PRs |

*(Keep `mcp/run.sh` and `mcp/run.bat` in `.gitignore` so secrets are never pushed to GitHub.)*

---

## 3. How to Run (`run.sh` and `run.bat`)

> **Note:** Never run `jira_mcp_server.py` or `github_mcp_server.py` manually. Each orchestrator automatically starts its MCP server in the background, discovers its tools, and closes it when finished.

### Option A: Git Bash (`run.sh`)
Always use `source` so exported environment variables stay active in your shell:

```bash
cd ~/Capstone/mcp

# 1. Load environment variables only
source run.sh

# 2. Load variables + run Jira agent
source run.sh jira

# 3. Load variables + run GitHub PR agent
source run.sh github
```

### Option B: Windows Command Prompt (`run.bat`)
```cmd
cd C:\Users\mahes\Capstone\mcp

:: 1. Set environment variables only
run.bat

:: 2. Set variables + run Jira agent
run.bat jira

:: 3. Set variables + run GitHub PR agent
run.bat github
```

---

## 4. How It Works

1. **Subprocess Handshake (`stdio`)**: When you start an orchestrator, it spawns its matching MCP server using Python over `stdio` JSON-RPC pipes (with `WindowsProactorEventLoopPolicy` and full environment inheritance enabled).
2. **Dynamic Tool Discovery**: The orchestrator queries `session.list_tools()` and binds the discovered tool schemas directly to Gemini (`gemini-3.8-flash` with `gemini-3.8-pro` fallback and a 120s timeout).
3. **Autonomous ReAct Loop**: Gemini reads your task prompt, calls the required Jira or GitHub tools step-by-step, inspects each API response, and outputs a final execution report.

---

## 5. Features & Available Tools

### Jira Features (`jira_mcp_server.py` — Project `CP`)
- **`list_open_jiras`**: Lists all open (`status != Done`) tickets with keys, types, and statuses.
- **`create_jira_ticket`**: Creates new issues and automatically formats descriptions into Atlassian Document Format (ADF).
- **`update_jira_fields`**: Updates the summary or description of an existing ticket (e.g., `CP-2`).
- **`add_jira_comment`**: Posts progress notes, logs, or PR URLs onto a ticket.
- **`update_jira_status`**: Dynamically resolves workflow transition IDs to move tickets across states (`To Do` → `In Progress` → `Done`).

### GitHub Features (`github_mcp_server.py` — `pavi550/Capstone`)
- **`list_repo_files`**: Lists files in the repo via GitHub's Git Trees API (supports path and extension filters).
- **`search_repo_code`**: Searches across the codebase for classes, functions, or keywords.
- **`read_file`**: Fetches and decodes file contents with line numbers so Gemini understands existing code before editing.
- **`create_branch`**: Cuts a new feature branch from `main` using the latest commit SHA.
- **`commit_file_change`**: Creates or updates files on a feature branch (automatically resolves existing file SHAs and blocks direct commits to `main`).
- **`create_pull_request`**: Opens a Pull Request into `main` and returns the direct GitHub PR link.
