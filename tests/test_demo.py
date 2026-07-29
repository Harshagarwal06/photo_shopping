from app.demo import demo_search


def test_demo_catalog_returns_realistic_known_products():
    products = demo_search("cheapest coffee")

    assert products
    assert all(product.in_stock for product in products)
    assert any("coffee" in product.name.casefold() for product in products)


def test_demo_catalog_returns_a_deterministic_relevant_synthetic_fallback():
    first = demo_search("toothbrush")
    second = demo_search("toothbrush")

    assert first == second
    assert len(first) == 1
    assert first[0].in_stock is True
    assert "toothbrush" in first[0].name.casefold()
    assert "demo product" in first[0].name.casefold()
