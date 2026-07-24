from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, safety_lock=False, dry_run=False, demo_mode=False)
    return Settings(**{**base, **overrides})


def test_zepto_writes_blocked_by_default():
    """zepto_cart_writes defaults off, so writes fail closed."""
    assert _settings().cart_mutations_allowed_for("zepto") is False


def test_zepto_writes_allowed_when_explicitly_enabled():
    assert _settings(zepto_cart_writes=True).cart_mutations_allowed_for("zepto") is True


def test_safety_lock_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, safety_lock=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_dry_run_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, dry_run=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_demo_mode_overrides_zepto_opt_in():
    settings = _settings(zepto_cart_writes=True, demo_mode=True)
    assert settings.cart_mutations_allowed_for("zepto") is False


def test_zepto_opt_in_does_not_leak_to_blinkit():
    """Enabling Zepto writes must not enable any other provider."""
    settings = _settings(zepto_cart_writes=True)
    assert settings.cart_mutations_allowed_for("instamart") is False


def test_zepto_has_its_own_browser_profile_dir():
    """Two Playwright providers cannot share one Chromium SingletonLock."""
    settings = _settings()
    assert settings.zepto_profile_dir != settings.browser_profile_dir


def test_comparison_defaults_match_constraints_threshold():
    settings = _settings()
    assert settings.min_fill_ratio == 0.9
    assert settings.eta_tiebreak_rupees == 20.0
