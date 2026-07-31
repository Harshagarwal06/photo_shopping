from __future__ import annotations

from ..config import Settings
from .base import GroceryProvider
from .blinkit import BlinkitProvider
from .demo import DemoProvider
from .instamart import InstamartProvider
from .zepto import ZeptoProvider


def create_provider(settings: Settings) -> GroceryProvider:
    return create_providers(settings)[settings.grocery_provider]


def create_providers(settings: Settings) -> dict[str, GroceryProvider]:
    """Create every supported provider; the setting only chooses the default."""
    if settings.demo_mode:
        return {
            "blinkit": DemoProvider("blinkit", "Blinkit"),
            "instamart": DemoProvider("instamart", "Swiggy Instamart"),
            "zepto": DemoProvider("zepto", "Zepto"),
        }
    return {
        "blinkit": BlinkitProvider(settings),
        "instamart": InstamartProvider(settings),
        "zepto": ZeptoProvider(settings),
    }
