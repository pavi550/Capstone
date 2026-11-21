"""Tool-bound LangChain adapters and a bounded, conversation-local fallback chain."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module

import httpx
from config import ConfigurationError, endpoint, number, required, settings
from gemini_transport import GeminiTransport
from langchain_core.messages import AIMessage
from langsmith import tracing_context

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    """A provider failure safe to display at the command line."""


@dataclass(frozen=True)
class Provider:
    module: str
    adapter: str
    key: str
    url: str | None
    model: str | None
    options: dict = field(default_factory=dict)
    transport_options: Callable | None = None
    legacy_fallback: str | None = None
    temperature_max: float = 2


def _openai_transport(timeout):
    return {"http_async_client": httpx.AsyncClient(
        follow_redirects=False, trust_env=False, timeout=timeout,
    )}


def _gemini_transport(_timeout):
    # The Gemini SDK can independently read these OS flags when initialized by LangChain.
    backend_vars = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI")
    if any(os.getenv(name, "").lower() in {"true", "1"} for name in backend_vars):
        raise ConfigurationError(
            "Unset GOOGLE_GENAI_USE_ENTERPRISE and GOOGLE_GENAI_USE_VERTEXAI for Gemini Developer API."
        )
    # Supplying a transport selects the SDK's httpx path even with aiohttp installed.
    return {"client_args": {
        "transport": GeminiTransport(), "follow_redirects": False, "trust_env": False,
    }}


OPENAI_OPTIONS = {"max_retries": 0, "use_responses_api": False}
PROVIDERS = {
    "openai": Provider(
        "langchain_openai", "ChatOpenAI", "OPENAI_API_KEY", "https://api.openai.com/v1",
        "gpt-4o-mini", OPENAI_OPTIONS, _openai_transport, "gpt-4o",
    ),
    "gemini": Provider(
        "langchain_google_genai", "ChatGoogleGenerativeAI", "GOOGLE_API_KEY",
        "https://generativelanguage.googleapis.com", "gemini-3.8-flash",
        {"vertexai": False, "max_retries": 1}, _gemini_transport, "gemini-3.8-pro",
    ),
    "anthropic": Provider(
        "anthropic_adapter", "SecureChatAnthropic", "ANTHROPIC_API_KEY",
        "https://api.anthropic.com", None, {"max_retries": 0}, temperature_max=1,
    ),
    "openai_compatible": Provider(
        "langchain_openai", "ChatOpenAI", "LLM_API_KEY", None, None, OPENAI_OPTIONS, _openai_transport,
    ),
}


def _selection(values, provider):
    selected = (provider or values.get("LLM_PROVIDER") or "gemini").strip().lower()
    raw_chain = values.get("LLM_FALLBACK_PROVIDERS", "").strip()
    chain = [item.strip().lower() for item in raw_chain.split(",")] if raw_chain else []
    if selected == "auto":
        chain = chain or ["gemini", "openai"]
    else:
        chain.insert(0, selected)
    if any(name not in PROVIDERS for name in chain) or len(chain) != len(set(chain)):
        choices = ", ".join(PROVIDERS)
        raise ConfigurationError(
            f"Check LLM_PROVIDER and LLM_FALLBACK_PROVIDERS. Use auto or providers from: {choices}."
        )
    return selected, chain


def _adapter(name, spec, values, model, tools, timeout):
    prefix = f"LLM_{name.upper()}_"
    url_var = prefix + "BASE_URL"
    if not values.get(url_var, "").strip() and spec.url is None:
        url_var = "LLM_BASE_URL"
    url = values.get(url_var, "").strip() or spec.url
    if url is None:
        url, = required(values, url_var)
    url = endpoint(url, url_var, allow_loopback=spec.url is None)
    key_var = prefix + "API_KEY"
    if not values.get(key_var, "").strip():
        key_var = spec.key
    # A custom native endpoint requires an explicitly paired key.
    if spec.url and url != spec.url:
        key_var = prefix + "API_KEY"
    key, = required(values, key_var)
    temp_var = prefix + "TEMPERATURE"
    if not values.get(temp_var, "").strip():
        temp_var = "LLM_TEMPERATURE"
    temperature = values.get(temp_var, "").strip()

    kwargs = dict(spec.options, model=model, api_key=key, base_url=url, timeout=timeout)
    if temperature:
        kwargs["temperature"] = number(temperature, temp_var, 0, spec.temperature_max)
    if spec.transport_options:
        kwargs.update(spec.transport_options(timeout))
    try:
        adapter = getattr(import_module(spec.module), spec.adapter)(**kwargs)
    except ImportError:
        raise ConfigurationError(f"Install MCP dependencies to use {name}.") from None
    except Exception:  # noqa: BLE001 - SDK diagnostics can contain credentials.
        raise ConfigurationError(
            f"Could not initialize {name}. Check its model, endpoint and optional settings."
        ) from None
    if tools and (adapter.profile or {}).get("tool_calling") is False:
        raise _tool_error(name)
    try:
        return adapter.bind_tools(tools) if tools else adapter
    except (NotImplementedError, AttributeError):
        raise _tool_error(name) from None
    except Exception:  # noqa: BLE001 - SDK diagnostics can contain credentials.
        raise ConfigurationError(f"Could not bind MCP tool schemas for {name}.") from None


def _tool_error(name):
    return LLMError(
        f"{name} did not support the required tool calls. Select a tool-capable model and endpoint."
    )


def _status(exc):
    status = getattr(exc, "status_code", getattr(exc, "code", None))
    if isinstance(status, int):
        return status
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None and type(exc).__module__.startswith("langchain_google_genai"):
        cause = exc.__cause__
        status = getattr(cause, "code", None)
    return status if isinstance(status, int) else None


def _retryable(exc, status):
    return status in RETRYABLE_STATUS or isinstance(
        exc, (TimeoutError, httpx.TimeoutException, httpx.NetworkError)
    ) or (type(exc).__module__.split(".")[0] in {"openai", "anthropic"}
          and type(exc).__name__ in {"APIConnectionError", "APITimeoutError"})


def _portable_history(messages, owners, candidate):
    return [
        AIMessage(content=message.text, tool_calls=message.tool_calls)
        if isinstance(message, AIMessage) and owners.get(id(message)) != candidate else message
        for message in messages
    ]


class ToolLLM:
    """One instance per workflow. This object never invokes or retries MCP tools."""

    def __init__(self, candidates, *, require_initial_tool_call=False):
        self._candidates = candidates
        self._active = 0
        self._owners = {}
        self._write_attempted = False
        self._require_initial_tool_call = require_initial_tool_call

    def mark_write_attempted(self):
        self._write_attempted = True

    async def ainvoke(self, messages):
        while True:
            name, client, timeout = self._candidates[self._active]
            history = _portable_history(messages, self._owners, self._active)
            try:
                with tracing_context(enabled=False):
                    response = await asyncio.wait_for(client.ainvoke(history), timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - classify and sanitize SDK errors.
                status = _status(exc)
                if status in {401, 403}:
                    raise LLMError(f"{name} rejected authentication or access. Check its key and permissions.") from None
                if status == 400:
                    detail = str(exc).lower()
                    if any(term in detail for term in ("tool", "function call")) and any(
                        term in detail for term in ("not support", "unsupported", "not enabled")
                    ):
                        raise _tool_error(name) from None
                if not _retryable(exc, status):
                    suffix = f" (HTTP {status})" if status else ""
                    raise LLMError(f"{name} request failed{suffix}. Check model and tool settings.") from None
                if self._write_attempted:
                    raise LLMError(
                        "The LLM failed after a write tool was attempted. "
                        "Check Jira or GitHub for the result before restarting."
                    ) from None
                if self._active + 1 == len(self._candidates):
                    raise LLMError("All eligible LLM attempts failed due to a provider outage, rate limit or timeout.") from None
            else:
                initial_tool_missing = (
                    self._require_initial_tool_call and len(messages) == 1 and not response.tool_calls
                )
                if response.invalid_tool_calls or initial_tool_missing:
                    raise _tool_error(name)
                self._owners[id(response)] = self._active
                return response
            await asyncio.sleep(min(0.25 * 2 ** self._active, 2))
            self._active += 1


def create_llm(
    tools=None, *, provider=None, model=None, values=None, require_initial_tool_call=False,
):
    values = settings() if values is None else values
    selected, chain = _selection(values, provider)
    timeout = number(
        values.get("LLM_TIMEOUT_SECONDS", "").strip() or "120", "LLM_TIMEOUT_SECONDS", 1, 600,
    )
    candidates = []
    for index, name in enumerate(chain):
        spec = PROVIDERS[name]
        model_var = f"LLM_{name.upper()}_MODEL"
        override = values.get(model_var, "").strip()
        if index == 0:
            override = model or override or values.get("LLM_MODEL", "").strip()
        chosen = override or spec.model
        if not chosen:
            raise ConfigurationError(f"Missing model configuration: set {model_var}.")
        candidates.append((name, _adapter(name, spec, values, chosen, tools, timeout), timeout))
        if len(chain) == 1 and selected != "auto" and not override and spec.legacy_fallback:
            alternate = _adapter(name, spec, values, spec.legacy_fallback, tools, timeout)
            candidates.append((name, alternate, timeout))
    return ToolLLM(candidates, require_initial_tool_call=require_initial_tool_call)


def get_llm_with_tools(llm_tools, **kwargs):
    return create_llm(llm_tools, require_initial_tool_call=True, **kwargs)
