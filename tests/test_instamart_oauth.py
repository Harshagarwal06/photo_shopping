import asyncio
import time
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import Settings
from app.providers.instamart_oauth import SwiggyOAuthClient
from app.providers.token_store import MemoryTokenStore


def test_oauth_uses_dcr_pkce_state_and_stores_token():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/auth/register":
            return httpx.Response(200, json={"client_id": "client-123"})
        if request.url.path == "/auth/token":
            return httpx.Response(
                200,
                json={"access_token": "token-abc", "expires_in": 3600},
            )
        raise AssertionError(f"Unexpected OAuth request: {request.url}")

    async def scenario():
        store = MemoryTokenStore()
        settings = Settings(
            _env_file=None,
            grocery_provider="instamart",
            swiggy_oauth_base_url="https://mcp.swiggy.test",
            swiggy_redirect_uri="http://localhost:8000/callback",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            oauth = SwiggyOAuthClient(settings, store, http_client=client)
            authorization_url = await oauth.authorization_url()
            query = parse_qs(urlparse(authorization_url).query)
            assert query["code_challenge_method"] == ["S256"]
            assert query["scope"] == ["mcp:tools"]
            assert query["state"][0]
            assert "code_challenge" in query

            record = await oauth.exchange_code("auth-code", query["state"][0])
            assert record.client_id == "client-123"
            assert record.access_token == "token-abc"
            assert record.expires_at > time.time()
            assert oauth.access_token() == "token-abc"

    asyncio.run(scenario())
    assert [request.url.path for request in requests] == ["/auth/register", "/auth/token"]
