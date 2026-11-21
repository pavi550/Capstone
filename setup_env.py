"""Install Capstone and guide a first-time tester through optional MCP setup."""

import argparse
import asyncio
import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
OLLAMA_URL = "http://127.0.0.1:11434/v1"
PROVIDERS = {
    "ollama": ("openai_compatible", "qwen3:8b", "LLM_API_KEY", OLLAMA_URL),
    "gemini": ("gemini", "gemini-3.8-flash", "GOOGLE_API_KEY", ""),
    "openai": ("openai", "gpt-4o-mini", "OPENAI_API_KEY", ""),
    "anthropic": ("anthropic", "claude-sonnet-4-6", "ANTHROPIC_API_KEY", ""),
    "groq": ("openai_compatible", "", "LLM_API_KEY", "https://api.groq.com/openai/v1"),
    "openrouter": ("openai_compatible", "", "LLM_API_KEY", "https://openrouter.ai/api/v1"),
    "compatible": ("openai_compatible", "", "LLM_API_KEY", ""),
}


def run_python(python, *args):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([str(python), *args], cwd=ROOT, env=env, check=True)


def install():
    local_venv = ROOT / ".venv"
    relative_python = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = local_venv / relative_python
    if not python.is_file():
        if (local_venv / "pyvenv.cfg").exists():
            raise RuntimeError("The existing .venv is broken. Rename it and rerun setup.")
        print("Creating the local Python environment...", flush=True)
        venv.EnvBuilder(with_pip=True).create(local_venv)
    try:
        run_python(python, "-c", "import sys; sys.exit(sys.version_info < (3, 11))")
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("The existing .venv needs Python 3.11+. Rename it and rerun setup.") from None
    print("Installing Capstone and test dependencies...", flush=True)
    run_python(python, "-m", "pip", "install", "--upgrade", "pip")
    # Build from a temporary copy so setuptools can generate metadata outside the checkout.
    with tempfile.TemporaryDirectory(prefix="capstone-setup-") as directory:
        source = Path(directory)
        for name in ("pyproject.toml", "README.md"):
            shutil.copyfile(ROOT / name, source / name)
        shutil.copytree(
            ROOT / "src", source / "src",
            ignore=shutil.ignore_patterns("*.egg-info", "__pycache__"),
        )
        run_python(
            python, "-m", "pip", "install", f"{source}[dev]", "-r", "mcp/requirements.txt",
        )
    return python


def ask(label, default="", *, secret=False):
    suffix = f" [{default}]" if default else ""
    while True:
        if secret:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                answer = getpass.getpass(f"{label} (hidden): ")
        else:
            answer = input(f"{label}{suffix}: ")
        answer = answer.strip() or default
        if answer:
            return answer
        print("Please enter a value.")


def choice(label, options, default):
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option}")
    choices = {str(index): option for index, option in enumerate(options, 1)}
    while True:
        answer = ask(label, str(options.index(default) + 1)).lower()
        if answer in choices:
            return choices[answer]
        if answer in options:
            return answer
        print("Choose a number from the list.")


def write_env(values):
    # python-dotenv supports single-quoted literals with escaped slashes and quotes.
    lines = ["# Private Capstone settings created by setup_env. Keep this file local."]
    for name, value in values.items():
        if "\r" in value or "\n" in value:
            raise RuntimeError(f"{name} must fit on a single line.")
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{name}='{escaped}'")
    descriptor = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def provider_settings(selected):
    from config import endpoint

    provider, model, key_var, default_url = PROVIDERS[selected]
    print("Use a model that supports tool calling and is enabled for your account.")
    values = {
        "LLM_PROVIDER": provider,
        "LLM_MODEL": ask("Model ID", model),
        "LLM_TIMEOUT_SECONDS": "180" if selected == "ollama" else "120",
        "LLM_FALLBACK_PROVIDERS": "",
    }
    if provider == "openai_compatible":
        url = default_url or ask("Provider base URL (including its API prefix)")
        values["LLM_BASE_URL"] = endpoint(url, "LLM_BASE_URL", allow_loopback=True)
    values[key_var] = "ollama" if selected == "ollama" else ask("Provider API key", secret=True)
    return values


def service_settings(selected):
    from config import github_settings, jira_settings

    if selected == "jira":
        print("Use an account that can browse project CP. The wizard will not query Jira.")
        values = {
            "JIRA_BASE_URL": ask("Jira site URL (https://your-domain.atlassian.net)"),
            "JIRA_EMAIL": ask("Jira account email"),
            "JIRA_API_TOKEN": ask("Jira API token", secret=True),
        }
        jira_settings(values)
        return values
    if selected == "github":
        print("Use a test repo. The GitHub agent can create branches, commits, and pull requests.")
        values = {
            "GITHUB_REPO": ask("Test GitHub repository (owner/repository)"),
            "GITHUB_BRANCH": ask("Base branch", "main"),
            "GITHUB_TOKEN": ask("GitHub token", secret=True),
        }
        github_settings(values)
        return values
    return {}


def configure():
    # Import the repository's modules without shadowing the installed MCP SDK.
    sys.path.insert(0, str(ROOT / "mcp"))
    from config import ConfigurationError, settings

    if ENV_PATH.exists():
        print("Your existing .env was kept. To change its settings, edit it locally.")
        if ask("Test the configured LLM now with mock data? Hosted APIs may charge (y/N)", "n").lower() == "y":
            try:
                check_llm(settings())
            except ConfigurationError as exc:
                raise RuntimeError(str(exc)) from None
        return
    print("\nChoose an LLM. Ollama runs locally. Hosted providers may charge for API use.")
    provider = choice("Provider", ("offline smoke test only", *PROVIDERS), "offline smoke test only")
    if provider == "offline smoke test only":
        print("The smoke test passed. Run setup again when you want to configure an LLM.")
        return

    try:
        values = provider_settings(provider)
        service = choice("Service to configure", ("none", "jira", "github"), "none")
        values.update(service_settings(service))
        write_env(values)
    except ConfigurationError as exc:
        raise RuntimeError(str(exc)) from None
    except FileExistsError:
        print("An existing .env was kept. Edit it locally to change its settings.")
        return

    print("Saved the private .env in the repository root.")
    if service != "none":
        python = r".\.venv\Scripts\python.exe" if os.name == "nt" else "./.venv/bin/python"
        print(f"To run the {service.title()} agent from this folder: {python} mcp/launch.py {service}")
        if os.name == "nt":
            print(f"In Git Bash: ./.venv/Scripts/python.exe mcp/launch.py {service}")
    if provider == "ollama":
        print("If Ollama is not ready, start it and use 'ollama list' to check your chosen model.")
        print("If the model is missing, install it manually with 'ollama pull <model>'.")
    if ask("Test the LLM now with mock data? Hosted APIs may charge (y/N)", "n").lower() == "y":
        check_llm(values)


def check_llm(values):
    from setup_health import check_provider

    if values.get("LLM_PROVIDER", "").strip().lower() == "auto":
        raise RuntimeError("Choose a single LLM_PROVIDER in .env before running the mock tool check.")
    if values.get("LLM_BASE_URL") == OLLAMA_URL and values.get("LLM_API_KEY") == "ollama":
        check_local_model(values)
    asyncio.run(check_provider({**values, "LLM_FALLBACK_PROVIDERS": ""}))


def check_local_model(values):
    import httpx
    from config import endpoint, required

    url, model = required(values, "LLM_BASE_URL", "LLM_MODEL")
    url = endpoint(url, "LLM_BASE_URL", allow_loopback=True)
    try:
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = client.get(f"{url}/models", headers={"Authorization": "Bearer ollama"})
            response.raise_for_status()
            model_ids = {item["id"] for item in response.json()["data"]}
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        raise RuntimeError("Local Ollama is unavailable. Start Ollama and run 'ollama list'.") from None
    if model not in model_ids:
        raise RuntimeError("The selected Ollama model is missing. Run 'ollama pull <model>' manually.")


def main():
    if sys.version_info < (3, 11):  # noqa: UP036 - bootstrap runs before installation.
        print("Install Python 3.11 or newer, then run setup again.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-only", action="store_true", help="Install and test without prompts.")
    parser.add_argument("--configure", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args()
    try:
        if options.configure:
            configure()
        else:
            python = install()
            print("Running the smoke test with a mock Jira server and mocked LLM...", flush=True)
            run_python(python, "-m", "pytest", "-q", "tests/test_ollama_integration.py", "-k", "mocked")
            if not options.smoke_only:
                run_python(python, str(Path(__file__).resolve()), "--configure")
    except (KeyboardInterrupt, EOFError, getpass.GetPassWarning):
        print("Setup stopped. For hidden credential input, run setup in an interactive terminal.")
        return 1
    except subprocess.CalledProcessError:
        print("Setup stopped because the previous step failed.", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("Setup could not access its local files or Python. Check permissions and retry.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
