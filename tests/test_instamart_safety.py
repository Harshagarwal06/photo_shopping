import asyncio

import pytest

from app.providers.base import ProviderSafetyError
from app.providers.instamart_transport import (
    ALLOWED_INSTAMART_TOOLS,
    InstamartMCPTransport,
)


def test_order_and_destructive_cart_tools_are_not_allowlisted():
    assert "checkout" not in ALLOWED_INSTAMART_TOOLS
    assert "clear_cart" not in ALLOWED_INSTAMART_TOOLS
    assert "create_address" not in ALLOWED_INSTAMART_TOOLS
    assert "delete_address" not in ALLOWED_INSTAMART_TOOLS


def test_blocked_tool_is_rejected_before_authentication_or_network_access():
    transport = InstamartMCPTransport("https://mcp.swiggy.test/im", oauth=None)

    with pytest.raises(ProviderSafetyError, match="blocked"):
        asyncio.run(transport.call_tool("checkout", {"addressId": "addr"}))
