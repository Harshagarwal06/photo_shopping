import asyncio

from app.blinkit import BlinkitClient
from app.config import Settings
from app.models import Product


def test_safety_lock_defaults_to_on():
    settings = Settings(_env_file=None)

    assert settings.safety_lock is True
    assert settings.cart_mutations_allowed is False


def test_safety_lock_blocks_cart_clicks_even_when_dry_run_is_off():
    settings = Settings(
        _env_file=None,
        dry_run=False,
        demo_mode=False,
        safety_lock=True,
    )
    client = BlinkitClient(settings)
    product = Product(
        id="safe-test",
        name="Test product",
        pack_size="1 pack",
        price=100,
        handle="safe-test",
    )

    result = asyncio.run(client.add_to_cart(product, 1))

    assert result.success is True
    assert result.dry_run is True
    assert "no Blinkit button was clicked" in result.message


def test_client_has_no_checkout_or_order_placement_action():
    client = BlinkitClient(Settings(_env_file=None))

    assert not hasattr(client, "checkout")
    assert not hasattr(client, "place_order")
