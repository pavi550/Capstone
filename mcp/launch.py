"""Command-line setup shared by the MCP orchestrators and shell launchers."""

import asyncio
import logging
import sys

from config import ConfigurationError
from llm_provider import LLMError


def print_report(value, values):
    report = str(value)
    secrets = {
        secret.strip() for name, secret in values.items()
        if name.endswith(("_TOKEN", "_API_KEY")) and secret.strip()
    }
    for secret in sorted(secrets, key=len, reverse=True):
        report = report.replace(secret, "[redacted]")
    print(report)


def run(workflow, args=None):
    args = list(sys.argv[1:] if args is None else args)
    if len(args) > 2:
        print("Expected at most a provider and a model.", file=sys.stderr)
        return 2
    provider, model = (args + [None, None])[:2]
    # The SDKs may log raw request and provider error details at debug levels.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(workflow(provider=provider, model=model))
    except Exception as exc:  # noqa: BLE001 - the CLI never prints raw exceptions.
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        message = str(exc) if isinstance(exc, (ConfigurationError, LLMError)) else (
            "MCP workflow failed. If a write was attempted, check its result before restarting."
        )
        print(message)
        return 1
    return 0


def main():
    # Dispatch lazily so the unused agent never validates credentials.
    args = sys.argv[1:]
    if not args:
        print("Usage: run.sh or run.bat [jira|github] [provider] [model]")
        return 0
    agent, *options = args
    if agent.lower() == "jira":
        from ai_orchestrator import run_ai_orchestrator
        return run(run_ai_orchestrator, options)
    if agent.lower() == "github":
        from github_orchestrator import run_github_pr_workflow
        return run(run_github_pr_workflow, options)
    print("Select jira or github.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
