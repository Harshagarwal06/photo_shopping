from app.providers.instamart import (
    addresses_from_instamart,
    cart_items_from_instamart,
    merge_cart_items,
    products_from_instamart,
)


def test_instamart_products_map_variants_to_existing_product_model():
    payload = {
        "products": [
            {
                "name": "Amul Taaza Toned Milk",
                "imageUrl": "https://example.test/milk.png",
                "variants": [
                    {
                        "spinId": "spin-milk-1l",
                        "packSize": "1 L",
                        "offerPrice": 56,
                        "mrp": 60,
                        "averageRating": 4.6,
                        "ratingCount": 2300,
                        "inStock": True,
                    }
                ],
            }
        ]
    }

    products = products_from_instamart(payload, "toned milk")

    assert len(products) == 1
    assert products[0].id == "spin-milk-1l"
    assert products[0].name == "Amul Taaza Toned Milk"
    assert products[0].pack_size == "1 L"
    assert products[0].price == 56
    assert products[0].mrp == 60
    assert products[0].rating == 4.6
    assert products[0].review_count == 2300
    assert products[0].search_query == "toned milk"


def test_instamart_products_map_current_live_nested_fields():
    payload = {
        "products": [
            {
                "displayName": "Nescafe Classic Instant Coffee",
                "isPromoted": True,
                "variations": [
                    {
                        "spinId": "spin-coffee",
                        "displayName": "Nescafe Classic Instant Coffee",
                        "quantityDescription": "24 g",
                        "price": {
                            "mrp": 124,
                            "offerPrice": 122,
                            "unitLevelPrice": "508.3/100 g",
                        },
                        "isInStockAndAvailable": True,
                        "imageUrl": "https://example.test/coffee.png",
                        "rating": {"value": "4.6", "count": "14.4k"},
                        "sla": {"value": "19", "unit": "MINS"},
                    }
                ],
            }
        ]
    }

    products = products_from_instamart(payload, "coffee")

    assert len(products) == 1
    assert products[0].id == "spin-coffee"
    assert products[0].name == "Nescafe Classic Instant Coffee"
    assert products[0].pack_size == "24 g"
    assert products[0].price == 122
    assert products[0].mrp == 124
    assert products[0].rating == 4.6
    assert products[0].review_count == 14_400
    assert products[0].delivery_minutes == 19
    assert products[0].in_stock is True
    assert products[0].sponsored is True


def test_cart_merge_preserves_existing_items_and_increments_matches():
    existing = {"spin-milk": 1, "spin-bread": 2}
    additions = {"spin-milk": 1, "spin-tissue": 1}

    assert merge_cart_items(existing, additions) == {
        "spin-milk": 2,
        "spin-bread": 2,
        "spin-tissue": 1,
    }


def test_cart_and_address_extractors_accept_nested_tool_data():
    cart = cart_items_from_instamart(
        {
            "cart": {
                "items": [
                    {"spinId": "spin-milk", "quantity": 2},
                    {"spinId": "spin-bread", "quantity": 1},
                ]
            }
        }
    )
    addresses = addresses_from_instamart(
        {
            "addresses": [
                {
                    "addressId": "addr-home",
                    "label": "Home",
                    "displayAddress": "Saved delivery address",
                }
            ]
        }
    )

    assert cart == {"spin-milk": 2, "spin-bread": 1}
    assert addresses[0].id == "addr-home"
    assert addresses[0].label == "Home"


def test_address_extractor_accepts_current_live_swiggy_fields():
    addresses = addresses_from_instamart(
        {
            "addresses": [
                {
                    "id": "addr-current",
                    "addressLine": "Saved delivery address",
                    "addressCategory": "RESIDENTIAL",
                    "addressTag": "Home",
                }
            ]
        }
    )

    assert len(addresses) == 1
    assert addresses[0].id == "addr-current"
    assert addresses[0].label == "Home"
    assert addresses[0].detail == "Saved delivery address"
