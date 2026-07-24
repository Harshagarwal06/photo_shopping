from app.config import Settings
from app.constraints import enforce_constraints
from app.models import CartConstraints
from app.providers.factory import create_providers


def test_blinkit_and_instamart_are_created_side_by_side():
    settings = Settings(_env_file=None, grocery_provider="blinkit")

    providers = create_providers(settings)

    assert set(providers) == {"blinkit", "instamart"}
    assert providers["blinkit"].display_name == "Blinkit"
    assert providers["instamart"].display_name == "Swiggy Instamart"


def test_provider_specific_cart_guards_do_not_disable_blinkit():
    settings = Settings(
        _env_file=None,
        grocery_provider="blinkit",
        dry_run=False,
        safety_lock=False,
        demo_mode=False,
        instamart_cart_writes=False,
    )

    assert settings.cart_mutations_allowed_for("blinkit") is True
    assert settings.cart_mutations_allowed_for("instamart") is False


def test_draft_keeps_the_provider_that_created_it():
    draft = enforce_constraints(
        [],
        CartConstraints(),
        dry_run=True,
        provider_id="blinkit",
        provider_name="Blinkit",
    )

    assert draft.provider_id == "blinkit"
    assert draft.provider_name == "Blinkit"
