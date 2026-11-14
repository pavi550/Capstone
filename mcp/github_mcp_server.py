import os
import base64
import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("Capstone-GitHub-SDLC-Server")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "pavi550").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "Capstone").strip()
DEFAULT_BASE_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip()

if not GITHUB_TOKEN or not GITHUB_OWNER:
    raise RuntimeError(
        "CRITICAL: GITHUB_TOKEN and GITHUB_OWNER must be set as environment variables before starting."
    )

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}
API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"


def _check_response(response: requests.Response, expected_codes: tuple[int, ...], action: str):
    """Validate GitHub REST API responses and raise clean MCP ToolErrors."""
    if response.status_code in (401, 403):
        raise ToolError(
            f"GitHub Auth/Permission Error (HTTP {response.status_code}) while {action}: "
            "Ensure GITHUB_TOKEN has 'repo' (classic) or 'Contents: Read & Write' + 'Pull requests: Read & Write' scopes."
        )
    if response.status_code == 404:
        raise ToolError(
            f"GitHub Not Found (404) while {action}. "
            f"Verify repo '{GITHUB_OWNER}/{GITHUB_REPO}', branch, or file path."
        )
    if response.status_code not in expected_codes:
        raise ToolError(f"GitHub API Error while {action} (HTTP {response.status_code}): {response.text}")


# ==============================================================================
# PHASE 1: READ & UNDERSTAND THE CODEBASE
# ==============================================================================

@mcp.tool()
def list_repo_files(branch: str = DEFAULT_BASE_BRANCH, path_prefix: str = "", extension: str = ".java") -> str:
    """List files in the GitHub repository on a given branch. Use path_prefix (e.g., 'src/main/java') or extension (e.g., '.java', '.xml') to filter."""
    url = f"{API_BASE}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=HEADERS, timeout=15)
    _check_response(response, (200,), f"listing files on branch '{branch}'")

    tree = response.json().get("tree", [])
    matched = []
    clean_prefix = path_prefix.strip("/")

    for item in tree:
        if item.get("type") != "blob":
            continue
        file_path = item.get("path", "")
        if clean_prefix and not file_path.startswith(clean_prefix):
            continue
        if extension and not file_path.endswith(extension):
            continue
        matched.append(file_path)
        if len(matched) >= 120:
            break

    if not matched:
        return f"No files matching extension '{extension}' under '{clean_prefix or '/'}' on '{branch}'."
    return "\n".join(matched)


@mcp.tool()
def search_repo_code(keyword: str) -> str:
    """Search for a class name, annotation, method, or keyword across the GitHub repository."""
    if not keyword.strip():
        raise ToolError("Search keyword cannot be empty.")

    url = "https://api.github.com/search/code"
    query = f"{keyword} repo:{GITHUB_OWNER}/{GITHUB_REPO}"
    response = requests.get(url, headers=HEADERS, params={"q": query, "per_page": 25}, timeout=15)
    _check_response(response, (200,), f"searching code for '{keyword}'")

    items = response.json().get("items", [])
    if not items:
        return f"No matches found for '{keyword}' in {GITHUB_OWNER}/{GITHUB_REPO}."

    return "\n".join(f"- {item.get('path')}" for item in items)


@mcp.tool()
def read_file(file_path: str, branch: str = DEFAULT_BASE_BRANCH, start_line: int = 1, end_line: int = 300) -> str:
    """Read source code lines from a file on GitHub (e.g., 'src/main/java/org/springframework/samples/petclinic/owner/Pet.java')."""
    clean_path = file_path.strip("/")
    url = f"{API_BASE}/contents/{clean_path}"
    response = requests.get(url, headers=HEADERS, params={"ref": branch}, timeout=15)
    _check_response(response, (200,), f"reading '{clean_path}' on '{branch}'")

    data = response.json()
    if isinstance(data, list):
        raise ToolError(f"'{clean_path}' is a directory. Use list_repo_files instead.")

    decoded = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    lines = decoded.splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = min(total, max(start, end_line))

    numbered = [f"{i}: {lines[i - 1]}" for i in range(start, end + 1)]
    return f"--- {clean_path} ({branch}, lines {start}-{end} of {total}) ---\n" + "\n".join(numbered)


# ==============================================================================
# PHASE 2: CREATE BRANCH, APPLY CODE CHANGES & RAISE PULL REQUEST
# ==============================================================================

@mcp.tool()
def create_branch(new_branch_name: str, base_branch: str = DEFAULT_BASE_BRANCH) -> str:
    """Create a new feature branch (e.g., 'feat/cp-2-pet-breed-weight') from the base branch before committing code changes."""
    if new_branch_name.strip() == base_branch.strip():
        raise ToolError("New branch name must be different from the base branch.")

    # 1. Get latest commit SHA of base_branch
    ref_url = f"{API_BASE}/git/ref/heads/{base_branch}"
    ref_res = requests.get(ref_url, headers=HEADERS, timeout=15)
    _check_response(ref_res, (200,), f"fetching SHA for base branch '{base_branch}'")
    base_sha = ref_res.json()["object"]["sha"]

    # 2. Create new branch reference
    create_url = f"{API_BASE}/git/refs"
    payload = {"ref": f"refs/heads/{new_branch_name.strip()}", "sha": base_sha}
    create_res = requests.post(create_url, headers=HEADERS, json=payload, timeout=15)

    if create_res.status_code == 422 and "Reference already exists" in create_res.text:
        return f"Branch '{new_branch_name}' already exists; ready for commits."

    _check_response(create_res, (201,), f"creating branch '{new_branch_name}'")
    return f"Successfully created branch '{new_branch_name}' from '{base_branch}' (SHA: {base_sha[:7]})."


@mcp.tool()
def commit_file_change(file_path: str, updated_full_content: str, commit_message: str, branch: str) -> str:
    """Create or update a file on a feature branch with complete, valid source code. Never commit directly to 'main'."""
    if branch.strip() in ("main", "master", DEFAULT_BASE_BRANCH):
        raise ToolError(f"Safety block: Direct commits to '{branch}' are forbidden. Create a feature branch first.")

    clean_path = file_path.strip("/")
    url = f"{API_BASE}/contents/{clean_path}"

    # 1. Check if file already exists on the branch to retrieve its blob SHA (required by GitHub API for updates)
    existing_sha = None
    get_res = requests.get(url, headers=HEADERS, params={"ref": branch}, timeout=15)
    if get_res.status_code == 200 and isinstance(get_res.json(), dict):
        existing_sha = get_res.json().get("sha")
    elif get_res.status_code not in (200, 404):
        _check_response(get_res, (200, 404), f"checking existing file '{clean_path}'")

    # 2. Encode updated content to Base64 and commit
    encoded_content = base64.b64encode(updated_full_content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "branch": branch.strip()
    }
    if existing_sha:
        payload["sha"] = existing_sha

    put_res = requests.put(url, headers=HEADERS, json=payload, timeout=15)
    _check_response(put_res, (200, 201), f"committing '{clean_path}' to '{branch}'")

    commit_sha = put_res.json().get("commit", {}).get("sha", "")[:7]
    action = "Updated" if existing_sha else "Created"
    return f"{action} '{clean_path}' on branch '{branch}' (Commit: {commit_sha})."


@mcp.tool()
def create_pull_request(title: str, body: str, head_branch: str, base_branch: str = DEFAULT_BASE_BRANCH) -> str:
    """Raise a Pull Request on GitHub merging head_branch into base_branch after committing changes."""
    url = f"{API_BASE}/pulls"
    payload = {
        "title": title,
        "body": body,
        "head": head_branch.strip(),
        "base": base_branch.strip()
    }
    response = requests.post(url, headers=HEADERS, json=payload, timeout=15)
    _check_response(response, (201,), f"raising Pull Request from '{head_branch}' to '{base_branch}'")

    pr_data = response.json()
    return f"Successfully raised PR #{pr_data.get('number')}: {pr_data.get('html_url')}"


if __name__ == "__main__":
    mcp.run()

