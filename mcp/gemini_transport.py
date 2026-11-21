"""HTTP transport usable by both clients constructed by the Gemini SDK."""

import httpx


class GeminiTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    def __init__(self):
        self._sync = httpx.HTTPTransport()
        self._async = httpx.AsyncHTTPTransport()

    def handle_request(self, request):
        return self._sync.handle_request(request)

    async def handle_async_request(self, request):
        return await self._async.handle_async_request(request)

    def close(self):
        self._sync.close()

    async def aclose(self):
        await self._async.aclose()
