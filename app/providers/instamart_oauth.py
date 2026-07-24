from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from ..config import Settings
from .base import ProviderAuthError, ProviderError
from .token_store import SwiggyTokenRecord, TokenStore


@dataclass(frozen=True)
class PendingAuthorization:
    verifier: str
    created_at: float


class SwiggyOAuthClient:
    def __init__(
        self,
        settings: Settings,
        token_store: TokenStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.settings = settings
        self.token_store = token_store
        self._http_client = http_client
        self._pending: dict[str, PendingAuthorization] = {}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._http_client is not None:
            response = await self._http_client.request(
                method,
                f"{self.settings.swiggy_oauth_base_url}{path}",
                **kwargs,
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    f"{self.settings.swiggy_oauth_base_url}{path}",
                    **kwargs,
                )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                payload = response.json()
                detail = payload.get("error_description") or payload.get("error") or ""
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise ProviderError(
                f"Swiggy authorization failed with HTTP {response.status_code}{suffix}"
            ) from exc
        return response

    async def _ensure_client_id(self) -> str:
        record = self.token_store.load()
        if record.client_id:
            return record.client_id
        response = await self._request(
            "POST",
            "/auth/register",
            json={
                "client_name": self.settings.app_name,
                "redirect_uris": [self.settings.swiggy_redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "mcp:tools",
            },
        )
        payload = response.json()
        client_id = str(payload.get("client_id") or "").strip()
        if not client_id:
            raise ProviderError("Swiggy did not return an OAuth client identifier.")
        record.client_id = client_id
        self.token_store.save(record)
        return client_id

    async def authorization_url(self) -> str:
        client_id = await self._ensure_client_id()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        self._pending[state] = PendingAuthorization(verifier=verifier, created_at=time.time())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": self.settings.swiggy_redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": "mcp:tools",
            }
        )
        return f"{self.settings.swiggy_oauth_base_url}/auth/authorize?{query}"

    async def exchange_code(self, code: str, state: str) -> SwiggyTokenRecord:
        pending = self._pending.pop(state, None)
        if pending is None or time.time() - pending.created_at > 120:
            raise ProviderAuthError(
                "The Instamart connection request expired or failed its security check. Retry Connect."
            )
        record = self.token_store.load()
        response = await self._request(
            "POST",
            "/auth/token",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": record.client_id,
                "code_verifier": pending.verifier,
                "redirect_uri": self.settings.swiggy_redirect_uri,
            },
        )
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ProviderAuthError("Swiggy did not return an access token.")
        expires_in = max(60, int(payload.get("expires_in") or 432_000))
        record.access_token = access_token
        record.expires_at = time.time() + expires_in
        self.token_store.save(record)
        return record

    def access_token(self) -> str:
        record = self.token_store.load()
        if (
            not record.access_token
            or not record.expires_at
            or record.expires_at <= time.time() + 60
        ):
            raise ProviderAuthError("Connect Instamart to continue.")
        return record.access_token

    def is_connected(self) -> bool:
        try:
            self.access_token()
            return True
        except ProviderAuthError:
            return False

    def selected_address_id(self) -> str | None:
        return self.token_store.load().selected_address_id

    def save_selected_address(self, address_id: str) -> None:
        record = self.token_store.load()
        record.selected_address_id = address_id
        self.token_store.save(record)

    async def logout(self) -> None:
        record = self.token_store.load()
        if record.access_token:
            try:
                await self._request(
                    "POST",
                    "/auth/logout",
                    headers={"Authorization": f"Bearer {record.access_token}"},
                )
            except ProviderError:
                # Local revocation still proceeds if Swiggy is temporarily unavailable.
                pass
        self.token_store.delete()
