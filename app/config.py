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
    nvidia_api_key: str | None = None
    nvidia_api_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model_id: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    nvidia_reasoning_budget: int = Field(default=128, ge=0, le=4096)
    groq_api_key: str | None = None
    groq_api_base_url: str = "https://api.groq.com/openai/v1"
    groq_model_id: str = "qwen/qwen3.6-27b"
    cloud_model_backend: Literal["auto", "hf", "nvidia", "groq"] = "auto"
    recognition_policy: Literal["review", "autonomous_safe"] = "review"
    autonomous_accept_confidence: float = Field(default=0.88, ge=0.5, le=1)
    autonomous_catalog_confidence: float = Field(default=0.70, ge=0.5, le=1)
    autonomous_catalog_min_providers: int = Field(default=2, ge=1, le=3)
    autonomous_max_hypotheses: int = Field(default=2, ge=1, le=5)
    local_vision_fallback: bool = True

    grocery_provider: Literal["blinkit", "instamart", "zepto"] = "blinkit"

    blinkit_base_url: str = "https://blinkit.com"
    browser_profile_dir: Path = ROOT / "browser_profile"
    browser_headless: bool = False
    dry_run: bool = True
    demo_mode: bool = False
    safety_lock: bool = True
    auto_add_to_cart: bool = False
    checkout_disabled: bool = True
    blinkit_cart_writes: bool = False
    instamart_cart_writes: bool = False
    instamart_mcp_url: str = "https://mcp.swiggy.com/im"
    swiggy_oauth_base_url: str = "https://mcp.swiggy.com"
    swiggy_redirect_uri: str = "http://localhost:8000/api/providers/instamart/callback"
    swiggy_keyring_service: str = "photo-shopping.swiggy"
    swiggy_keyring_account: str = "local-user"
    zepto_base_url: str = "https://www.zeptonow.com"
    zepto_profile_dir: Path = ROOT / "browser_profile_zepto"
    zepto_cart_writes: bool = False
    min_fill_ratio: float = Field(default=0.9, gt=0, le=1)
    max_fill_ratio: float = Field(default=1.1, ge=1)
    eta_tiebreak_rupees: float = Field(default=20.0, ge=0)
    comparison_confirmation_ttl_seconds: int = Field(default=300, ge=30, le=1800)
    max_state_records: int = Field(default=200, ge=10, le=5000)
    # Long enough to cover recognition searching a query and the draft or
    # comparison run searching it again moments later. 0 disables the cache.
    search_cache_ttl_seconds: float = Field(default=90.0, ge=0, le=900)
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
    def cloud_backend(self) -> Literal["hf", "nvidia", "groq"] | None:
        """Resolve the requested cloud provider, preferring Groq vision in auto mode."""
        if self.cloud_model_backend == "groq":
            return "groq" if self.groq_api_key else None
        if self.cloud_model_backend == "nvidia":
            return "nvidia" if self.nvidia_api_key else None
        if self.cloud_model_backend == "hf":
            return "hf" if self.hf_token else None
        if self.groq_api_key:
            return "groq"
        if self.nvidia_api_key:
            return "nvidia"
        if self.hf_token:
            return "hf"
        return None

    @property
    def cloud_model(self) -> str:
        if self.cloud_backend == "groq":
            return self.groq_model_id
        if self.cloud_backend == "nvidia":
            return self.nvidia_model_id
        return self.planner_model

    def model_backend_configured(self, backend: str | None = None) -> bool:
        selected = backend or self.model_backend
        if selected == "local":
            return True
        if selected == "hf":
            return bool(self.hf_token)
        if selected == "nvidia":
            return bool(self.nvidia_api_key)
        if selected == "groq":
            return bool(self.groq_api_key)
        return False

    @property
    def cart_mutations_allowed(self) -> bool:
        """Compatibility helper for the configured default provider."""
        return self.cart_mutations_allowed_for(self.grocery_provider)

    def cart_mutations_allowed_for(self, provider_id: str) -> bool:
        """Fail closed unless global and provider-specific guards permit writes."""
        base_allowed = not self.safety_lock and not self.dry_run and not self.demo_mode
        provider_gates = {
            "blinkit": self.blinkit_cart_writes,
            "instamart": self.instamart_cart_writes,
            "zepto": self.zepto_cart_writes,
        }
        return base_allowed and provider_gates.get(provider_id, False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
