# MCP agents for Jira and GitHub

The Jira orchestrator lists and summarizes open issues in project CP. The GitHub
orchestrator inspects the configured repository, creates a feature branch,
writes `ARCHITECTURE.md` there, and opens a pull request. Running the GitHub
agent against a live repository can make those changes.

## Layout and architecture

- `config.py` reads the repository-root `.env` and process environment.
- `llm_provider.py` resolves the model and binds MCP tool schemas to a LangChain
  adapter. Each workflow receives the same asynchronous `ainvoke(messages)`
  interface.
- `anthropic_adapter.py` configures the native Anthropic async transport to
  reject redirects. `gemini_transport.py` applies the same policy to Gemini.
- `ai_orchestrator.py` and `github_orchestrator.py` discover tools over MCP stdio,
  execute each returned tool call, and pass text results back as tool messages.
  Only the selected service's configuration is passed to its child process.
- `jira_mcp_server.py` and `github_mcp_server.py` define the REST-backed tools.
- `run.sh` and `run.bat` dispatch through `launch.py`. Tests live in `tests/test_mcp.py`.

## Install and configure

Use Python 3.11 or newer. From the repository root, install the MCP SDK v2,
LangChain integrations, python-dotenv, and HTTP dependencies in your activated
virtual environment.

```text
python -m pip install -r mcp/requirements.txt
```

Make a private root `.env` using `copy .env.example .env` in CMD or
`cp .env.example .env` in Git Bash. Each developer configures their own clone
and can change providers without editing Python or the launchers. The loader
locates this file by the source path from any working directory. A missing
`.env` is fine if the process environment supplies the settings. File values
are literal, with dotenv quoting and comments supported. Process environment
variables override same-named file values, including empty values.

| Agent | Settings | Credential source |
| --- | --- | --- |
| Jira | `JIRA_BASE_URL` (HTTPS), `JIRA_EMAIL`, `JIRA_API_TOKEN` | [Atlassian API tokens](https://id.atlassian.com/manage-profile/security/api-tokens) |
| GitHub | `GITHUB_TOKEN`, optional `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` | [GitHub personal access tokens](https://github.com/settings/tokens) |
| Gemini | `GOOGLE_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |
| OpenAI | `OPENAI_API_KEY` | [OpenAI API keys](https://platform.openai.com/api-keys) |
| Anthropic | `ANTHROPIC_API_KEY` | [Anthropic Console](https://console.anthropic.com/settings/keys) |
| OpenAI-compatible | `LLM_API_KEY`, `LLM_BASE_URL` | Your endpoint operator |

Use a Jira token issued for the configured email. The GitHub demo needs
Contents and Pull requests read/write permissions for the target repository.
GitHub defaults to `pavi550/Capstone`, branch `main`. `GITHUB_REPO` also accepts
`owner/repository`, which takes precedence over `GITHUB_OWNER`.

## Select a model

| `LLM_PROVIDER` | Default model | Adapter |
| --- | --- | --- |
| `gemini` (default provider) | `gemini-3.8-flash` | Gemini Developer API |
| `openai` | `gpt-4o-mini` | OpenAI Chat Completions |
| `anthropic` | Set a tool-capable Claude model | Anthropic Messages |
| `openai_compatible` | Set a tool-capable model | OpenAI Chat Completions |
| `auto` | Defaults for the first provider in the chain | Ordered chain |

For the primary, model precedence is CLI model, then `LLM_<PROVIDER>_MODEL`,
then `LLM_MODEL`, then the table default. Subsequent providers use their own
`LLM_<PROVIDER>_MODEL` or default. The CLI provider overrides `LLM_PROVIDER`.
Names in per-provider variables use uppercase, e.g. `LLM_ANTHROPIC_MODEL`.

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
ANTHROPIC_API_KEY=
```

`LLM_TIMEOUT_SECONDS` is the per-attempt limit (1–600 seconds, default 120).
Leave `LLM_TEMPERATURE` empty to use the adapter/model default. The numeric range
is 0–2 for Gemini and OpenAI-style adapters and 0–1 for Anthropic. Set
`LLM_<PROVIDER>_TEMPERATURE` for a specific adapter. Some models reject sampling
overrides, so leave the relevant temperature empty for those models.

### OpenAI-compatible endpoints

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=your-tool-capable-model
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=
```

Use your provider's Chat Completions base URL, without credentials, query
parameters, or fragments. Examples include
`https://api.groq.com/openai/v1` and `https://openrouter.ai/api/v1`. For a local
Ollama service, use `http://localhost:11434/v1` with a tool-capable installed
model and a non-secret value such as `ollama` as the key if your local server
ignores authentication. Compatible endpoints accept plain HTTP only on
`localhost`, `127.0.0.1`, or `::1`. Test that your model returns standard
Chat Completions tool calls and accepts tool results.

`LLM_OPENAI_COMPATIBLE_BASE_URL` and `LLM_OPENAI_COMPATIBLE_API_KEY` override the
generic values. To use a non-default HTTPS endpoint for a native provider, set
both `LLM_<PROVIDER>_BASE_URL` and `LLM_<PROVIDER>_API_KEY`. Standard native keys
are used only with trusted native defaults. The selected native endpoint still
needs to implement that provider's protocol. Azure API type/version settings
and Vertex AI authentication are outside the implemented interfaces. Unset
`GOOGLE_GENAI_USE_ENTERPRISE` and `GOOGLE_GENAI_USE_VERTEXAI` for the Gemini Developer API.

### Local Ollama with Qwen3

Install [Ollama for Windows](https://ollama.com/download/windows) if needed.
Run `ollama list` to check whether `qwen3:8b` is installed. If the local
server is stopped, start it with `ollama serve` in a separate terminal.
Download the model manually if it is missing (approximately 5.2 GB).

```text
ollama pull qwen3:8b
ollama list
```

Set these values in the repository-root `.env` to use the existing
OpenAI-compatible adapter with the Jira or GitHub agent.

```dotenv
LLM_PROVIDER=openai_compatible
LLM_MODEL=qwen3:8b
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=ollama
LLM_TIMEOUT_SECONDS=180
LLM_FALLBACK_PROVIDERS=
```

The adapter calls `/v1/chat/completions`. The client needs a nonempty
placeholder key. Local Ollama ignores it. See the [Ollama compatibility
reference](https://docs.ollama.com/api/openai-compatibility).

Run `source run.sh jira openai_compatible` in Git Bash or
`run.bat jira openai_compatible` in CMD from `mcp` to query your configured
Jira service. For a smoke test without Jira credentials, run the following
from the repository root after installing the MCP and test dependencies.

**Git Bash**

```bash
CAPSTONE_TEST_OLLAMA=1 python -m pytest -q -s -rs tests/test_ollama_integration.py
```

**Windows CMD**

```bat
set "CAPSTONE_TEST_OLLAMA=1"
python -m pytest -q -s -rs tests/test_ollama_integration.py
set "CAPSTONE_TEST_OLLAMA="
```

The opt-in test uses only local Ollama and an MCP subprocess that registers
one read-only Jira fixture tool. It checks that the orchestrator calls the
tool once and that the final answer reports the fixture issue and status.
On success it prints end-to-end latency, including model loading, both
model turns, and MCP subprocess overhead. The test skips if the service
or model is unavailable and never downloads a model. The default suite
also runs the same workflow with mocked Chat Completions responses.

## Automatic fallback

```dotenv
LLM_PROVIDER=auto
LLM_FALLBACK_PROVIDERS=gemini,openai,anthropic
LLM_ANTHROPIC_MODEL=claude-sonnet-4-6
GOOGLE_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
```

With an empty chain, `auto` uses Gemini then OpenAI. For an explicit provider,
a configured fallback list follows it. For a standalone default Gemini or
OpenAI run with no model override, the legacy same-provider Pro or GPT-4o
backup remains available. Configure credentials for the selected providers
only, including every configured fallback.

Each candidate gets one model attempt, with bounded backoff between candidates
on timeouts, connection errors, rate limits, or transient provider HTTP
failures. Authentication problems, invalid requests, and tool schema failures
stop the run. A successful fallback becomes the active model for subsequent
turns. The conversation retains human messages, assistant text, tool calls,
and tool results when switching providers. Provider-specific metadata such
as hidden reasoning signatures stays with its originating provider.

Only model requests can fall back. After the orchestrator attempts a write
tool, including a write whose outcome is uncertain, a model failure ends the
workflow. Check Jira or GitHub before starting the workflow again. Configuring
fallback allows the listed providers to receive the conversation and MCP tool
results.

## Launch

From `Capstone/mcp` in Git Bash:

```bash
source run.sh
source run.sh jira
source run.sh jira openai
source run.sh jira anthropic
source run.sh github openai
source run.sh github anthropic
source run.sh github openai_compatible
source run.sh github auto
```

From `Capstone\mcp` in Windows CMD:

```bat
run.bat
run.bat jira
run.bat jira anthropic
run.bat github openai
run.bat github openai_compatible
run.bat github auto
```

With no arguments the launcher prints usage. Agent runs load their settings in
Python and keep credentials out of the parent shell. An optional third argument
selects the primary model, for example `run.bat jira openai gpt-4o-mini`.
The scripts also accept being invoked by path from another directory.

## Troubleshooting and credential rotation

A missing-setting error gives the variable names to populate. Check the
selected agent and fallback chain, then check for an empty process environment
variable overriding your file. If the provider rejects tool calls, select a
model and endpoint that support function calling. Command-line errors omit
provider response bodies and raw SDK exception traces.

Git ignores local `.env` files and their variants at any depth. Keep
`.env.example` placeholder-only. If you used credentials previously stored in
the launchers or Jira source, revoke and replace them with the issuing service.
Old commits still contain the original values. Put rotated values in your
private `.env` or process environment.

## Add an adapter and run tests

Register a `Provider` in `llm_provider.py` with a LangChain model class, key
variable, trusted default URL (if any), and provider-specific options. Wrap
native SDK differences in an adapter exposing `bind_tools` and async
`ainvoke`. Verify request translation, tool call normalization, trusted
endpoint handling, and sanitized errors with synthetic credentials and mocked
network responses. The agents use asynchronous tool calling with complete model responses. Token streaming, structured model output, and provider-hosted
tools are outside the current workflow.

```text
python -m pip install -e ".[dev]"
python -m pytest
```

The tests exercise configuration, real adapter construction and tool binding,
mocked LLM failure and response handling, MCP tool discovery, and Jira/GitHub
response contracts. Live provider model availability and service permissions
depend on the developer's account.
