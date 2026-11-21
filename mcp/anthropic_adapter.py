"""Transport customizations for the asynchronous MCP provider adapters."""

from functools import cached_property

import anthropic
from langchain_anthropic import ChatAnthropic


class SecureChatAnthropic(ChatAnthropic):
    """Restrict the async SDK transport to the selected endpoint."""

    @cached_property
    def _async_client(self):
        params = self._client_params
        return anthropic.AsyncClient(
            **params,
            http_client=anthropic.DefaultAsyncHttpxClient(
                follow_redirects=False,
                trust_env=False,
                timeout=params["timeout"],
            ),
        )
