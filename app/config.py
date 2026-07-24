from functools import lru_cache
from pathlib import Path

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Photo Shopping"
    model_backend: str = "hf"
    model_id: str = "Qwen/Qwen2.5-VL-3B-Instruct"
    planner_model_id: str | None = None
    matcher_model_id: str | None = None
    hf_token: str | None = None
    hf_provider: str = "auto"
    local_vision_fallback: bool = True

    grocery_provider: Literal["blinkit", "instamart"] = "blinkit"

    blinkit_base_url: str = "https://blinkit.com"
    browser_profile_dir: Path = ROOT / "browser_profile"
    browser_headless: bool = False
    dry_run: bool = True
    demo_mode: bool = False
    safety_lock: bool = True
    auto_add_to_cart: bool = False
    checkout_disabled: bool = True
    instamart_cart_writes: bool = False
    instamart_mcp_url: str = "https://mcp.swiggy.com/im"
    swiggy_oauth_base_url: str = "https://mcp.swiggy.com"
    swiggy_redirect_uri: str = "http://localhost:8000/api/providers/instamart/callback"
    swiggy_keyring_service: str = "photo-shopping.swiggy"
    swiggy_keyring_account: str = "local-user"
    order_history_limit: int = Field(default=5, ge=0, le=20)
    search_result_limit: int = Field(default=5, ge=1, le=10)
    navigation_timeout_ms: int = Field(default=30_000, ge=5_000)

    @property
    def planner_model(self) -> str:
        return self.planner_model_id or self.model_id

    @property
    def matcher_model(self) -> str:
        return self.matcher_model_id or self.model_id

    @property
    def cart_mutations_allowed(self) -> bool:
        """Compatibility helper for the configured default provider."""
        return self.cart_mutations_allowed_for(self.grocery_provider)

    def cart_mutations_allowed_for(self, provider_id: str) -> bool:
        """Fail closed unless global and provider-specific guards permit writes."""
        base_allowed = not self.safety_lock and not self.dry_run and not self.demo_mode
        if provider_id == "instamart":
            return base_allowed and self.instamart_cart_writes
        return base_allowed


@lru_cache
def get_settings() -> Settings:
    return Settings()
