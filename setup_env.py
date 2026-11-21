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
        try:
            venv.EnvBuilder(with_pip=True).create(local_venv)
        except subprocess.CalledProcessError:
            raise RuntimeError(
                "Python could not create a virtual environment with pip. On Debian or Ubuntu, "
                "install python3-venv with sudo apt install python3-venv. On Windows or macOS, "
                "use the installer at https://www.python.org/downloads/. "
                "Rename the incomplete .venv and rerun setup."
            ) from None
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
    from dotenv import set_key

    for name, value in values.items():
        if "\r" in value or "\n" in value:
            raise RuntimeError(f"{name} must fit on a single line.")
    if ENV_PATH.is_symlink():
        raise RuntimeError("Edit the linked .env file directly.")
    original = ENV_PATH.read_bytes() if ENV_PATH.exists() else None
    if original is None:
        # Exclusive creation prevents replacing a file created while the wizard was open.
        fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    with tempfile.TemporaryDirectory(prefix=".env.setup-", dir=ENV_PATH.parent) as directory:
        updated = Path(directory) / "settings"
        header = b"# Private Capstone settings. Keep this file local.\n"
        updated.write_bytes(original if original is not None else header)
        for name, value in values.items():
            set_key(updated, name, value, encoding="utf-8-sig", quote_mode="always")
        if ENV_PATH.read_bytes() != (original or b""):
            raise RuntimeError("The .env changed during setup. Rerun the wizard.")
        updated.chmod(0o600)
        os.replace(updated, ENV_PATH)


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
    from config import confluence_settings, github_settings, jira_settings

    if selected == "jira":
        print("Use an account that can browse project CP. The wizard will not query Jira.")
        values = {
            "JIRA_BASE_URL": ask("Jira site URL (https://your-domain.atlassian.net)"),
            "JIRA_EMAIL": ask("Jira account email"),
            "JIRA_API_TOKEN": ask("Jira API token", secret=True),
        }
        jira_settings(values)
        return values
    if selected == "confluence":
        print("Confluence setup enables a read-only Cloud connection check.")
        values = {
            "CONFLUENCE_BASE_URL": ask("Confluence site URL (https://your-domain.atlassian.net/wiki)"),
            "CONFLUENCE_EMAIL": ask("Confluence account email"),
            "CONFLUENCE_API_TOKEN": ask("Confluence API token", secret=True),
        }
        confluence_settings(values)
        return values
    if selected == "github":
        print("Use a test repo. The GitHub agent can create branches, commits, and pull requests.")
        print("Create a GitHub personal access token at https://github.com/settings/tokens.")
        print("Grant Contents and Pull requests read/write access for the selected repository.")
        values = {
            "GITHUB_REPO": ask("Test GitHub repository (owner/repository)"),
            "GITHUB_BRANCH": ask("Base branch", "main"),
            "GITHUB_TOKEN": ask("GitHub personal access token", secret=True),
        }
        github_settings(values)
        return values
    return {}


def model_chain(selected):
    values = provider_settings(selected)
    primary = values["LLM_PROVIDER"]
    # Each runtime slot has independent settings. Compatible presets currently share a slot.
    options = ("none", *(name for name, spec in PROVIDERS.items() if spec[0] != primary))
    fallback = choice("Backup LLM if the primary has a temporary failure", options, "none")
    if fallback != "none":
        backup = provider_settings(fallback)
        name = backup["LLM_PROVIDER"]
        prefix = f"LLM_{name.upper()}_"
        values["LLM_FALLBACK_PROVIDERS"] = name
        values[prefix + "MODEL"] = backup["LLM_MODEL"]
        if name == "openai_compatible":
            values[prefix + "BASE_URL"] = backup["LLM_BASE_URL"]
            values[prefix + "API_KEY"] = backup["LLM_API_KEY"]
        else:
            key_var = PROVIDERS[fallback][2]
            values[prefix + "API_KEY"] = backup[key_var]
            values[prefix + "BASE_URL"] = ""
        values["LLM_TIMEOUT_SECONDS"] = str(max(
            int(values["LLM_TIMEOUT_SECONDS"]), int(backup["LLM_TIMEOUT_SECONDS"]),
        ))
    # Explicit provider-specific values take precedence over prior wizard/manual overrides.
    prefix = f"LLM_{primary.upper()}_"
    values[prefix + "MODEL"] = values["LLM_MODEL"]
    values[prefix + "API_KEY"] = values[PROVIDERS[selected][2]]
    values[prefix + "BASE_URL"] = values.get("LLM_BASE_URL", "")
    return values


def configure():
    sys.path.insert(0, str(ROOT / "mcp"))
    from config import settings

    if ENV_PATH.exists():
        print("Your existing .env is available. Only the settings you choose will be updated.")
    action = choice(
        "Next step",
        ("finish", "configure llm and services", "configure services only", "test configured llm"),
        "finish",
    )
    if action == "finish":
        return
    if action == "test configured llm":
        if ask("Send mock data to the selected provider now? Hosted APIs may charge (y/N)", "n").lower() == "y":
            check_llm(settings())
        return

    values = {}
    selected = None
    if action == "configure llm and services":
        print("Ollama runs locally. Hosted providers may charge for API use.")
        selected = choice("Primary provider", tuple(PROVIDERS), "ollama")
        values.update(model_chain(selected))
    service_options = {
        "none": (),
        "jira": ("jira",),
        "confluence": ("confluence",),
        "jira and confluence": ("jira", "confluence"),
        "github": ("github",),
        "jira, confluence, and github": ("jira", "confluence", "github"),
    }
    service = choice("Services to configure", tuple(service_options), "none")
    services = service_options[service]
    for name in services:
        values.update(service_settings(name))
    if not values:
        return
    write_env(values)
    print("Saved the selected settings in the private .env.")
    python = r".\.venv\Scripts\python.exe" if os.name == "nt" else "./.venv/bin/python"
    for name in services:
        print(f"Run from the repository root: {python} mcp/launch.py {name}")
        if os.name == "nt":
            print(f"In Git Bash: ./.venv/Scripts/python.exe mcp/launch.py {name}")
    if selected == "ollama":
        print("Start Ollama and run 'ollama list'. If needed, install the model with 'ollama pull'.")
    if selected and ask("Test the primary LLM with mock data? Hosted APIs may charge (y/N)", "n").lower() == "y":
        check_llm(settings())


def check_llm(values):
    from setup_health import check_provider

    if values.get("LLM_PROVIDER", "").strip().lower() == "auto":
        raise RuntimeError("Choose a single LLM_PROVIDER in .env before running the mock tool check.")
    if values.get("LLM_PROVIDER", "").strip().lower() == "openai_compatible":
        base_url = values.get("LLM_OPENAI_COMPATIBLE_BASE_URL") or values.get("LLM_BASE_URL")
        key = values.get("LLM_OPENAI_COMPATIBLE_API_KEY") or values.get("LLM_API_KEY")
        if base_url == OLLAMA_URL and key == "ollama":
            model = values.get("LLM_OPENAI_COMPATIBLE_MODEL") or values.get("LLM_MODEL", "")
            check_local_model({"LLM_BASE_URL": base_url, "LLM_MODEL": model})
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
        print(
            "Install Python 3.11 or newer from https://www.python.org/downloads/. "
            "On Windows, add Python to PATH in the installer. Open a new terminal and rerun setup.",
            file=sys.stderr,
        )
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
