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
