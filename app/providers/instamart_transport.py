from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from .base import ProviderAuthError, ProviderError, ProviderSafetyError
from .instamart_oauth import SwiggyOAuthClient


ALLOWED_INSTAMART_TOOLS = frozenset(
    {
        "get_addresses",
        "search_products",
        "your_go_to_items",
        "get_cart",
        "update_cart",
        "get_orders",
        "get_order_details",
    }
)


class InstamartMCPTransport:
    def __init__(self, endpoint: str, oauth: SwiggyOAuthClient):
        self.endpoint = endpoint
        self.oauth = oauth

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict:
        if name not in ALLOWED_INSTAMART_TOOLS:
            raise ProviderSafetyError(f"Instamart tool is blocked: {name}")
        token = self.oauth.access_token()
        try:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(45.0),
            ) as http_client:
                async with streamable_http_client(
                    self.endpoint,
                    http_client=http_client,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=45),
                    ) as session:
                        await session.initialize()
                        result = await session.call_tool(name, arguments or {})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 419}:
                raise ProviderAuthError("The Instamart session expired. Reconnect to continue.") from exc
            raise ProviderError(
                f"Instamart returned HTTP {exc.response.status_code}."
            ) from exc
        except ProviderError:
            raise
        except Exception as exc:
            message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            if "401" in message or "419" in message:
                raise ProviderAuthError("The Instamart session expired. Reconnect to continue.") from exc
            raise ProviderError(f"Instamart MCP request failed: {message}") from exc

        payload = self._result_payload(result)
        if result.isError:
            message = self._message(payload) or f"Instamart tool {name} failed."
            raise ProviderError(message)
        if payload.get("success") is False:
            raise ProviderError(self._message(payload) or f"Instamart tool {name} failed.")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _result_payload(result) -> dict:
        if result.structuredContent:
            return dict(result.structuredContent)
        for content in result.content:
            if isinstance(content, TextContent):
                try:
                    payload = json.loads(content.text)
                except json.JSONDecodeError:
                    return {"message": content.text}
                if isinstance(payload, dict):
                    return payload
                return {"data": payload}
        return {}

    @staticmethod
    def _message(payload: dict) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or "")
        return str(payload.get("message") or error or "")
