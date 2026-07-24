from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError
from pydantic import BaseModel

from .base import ProviderError


class SwiggyTokenRecord(BaseModel):
    client_id: str | None = None
    access_token: str | None = None
    expires_at: float | None = None
    selected_address_id: str | None = None


class TokenStore(Protocol):
    def load(self) -> SwiggyTokenRecord:
        ...

    def save(self, record: SwiggyTokenRecord) -> None:
        ...

    def delete(self) -> None:
        ...


class KeyringTokenStore:
    def __init__(self, service: str, account: str):
        self.service = service
        self.account = account

    def load(self) -> SwiggyTokenRecord:
        try:
            raw = keyring.get_password(self.service, self.account)
        except KeyringError as exc:
            raise ProviderError(
                "The macOS Keychain could not be opened for the Instamart session."
            ) from exc
        if not raw:
            return SwiggyTokenRecord()
        try:
            return SwiggyTokenRecord.model_validate_json(raw)
        except Exception as exc:
            raise ProviderError("The saved Instamart session is invalid; disconnect and retry.") from exc

    def save(self, record: SwiggyTokenRecord) -> None:
        try:
            keyring.set_password(self.service, self.account, record.model_dump_json())
        except KeyringError as exc:
            raise ProviderError(
                "The Instamart session could not be saved securely in macOS Keychain."
            ) from exc

    def delete(self) -> None:
        try:
            keyring.delete_password(self.service, self.account)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise ProviderError("The Instamart Keychain session could not be removed.") from exc


class MemoryTokenStore:
    """Test-only token storage."""

    def __init__(self, record: SwiggyTokenRecord | None = None):
        self.record = record or SwiggyTokenRecord()

    def load(self) -> SwiggyTokenRecord:
        return self.record.model_copy(deep=True)

    def save(self, record: SwiggyTokenRecord) -> None:
        self.record = record.model_copy(deep=True)

    def delete(self) -> None:
        self.record = SwiggyTokenRecord()
