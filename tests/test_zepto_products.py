from app.zepto import zepto_products_from_raw


def test_parses_product_cards():
    raw = [
        {
            "text": "Amul Taaza Toned Milk\n1 L\n₹75\n₹80\n6% OFF\nAdd",
            "href": "/pn/amul-taaza/pvid/abc123",
            "image": "https://cdn.zeptonow.com/milk.png",
            "addText": "Add",
        },
        {
            "text": "Mother Dairy Milk\n500 ml\n₹42\nOut of stock",
            "href": "/pn/mother-dairy/pvid/def456",
            "image": "https://cdn.zeptonow.com/md.png",
            "addText": "Out of stock",
        },
    ]

    products = zepto_products_from_raw("milk", raw, limit=5,
                                       base_url="https://www.zeptonow.com")

    assert [p.name for p in products] == ["Amul Taaza Toned Milk", "Mother Dairy Milk"]
    assert products[0].pack_size == "1 L"
    assert products[0].price == 75.0
    assert products[0].mrp == 80.0
    assert products[0].discount_percent == 6.0
    assert products[0].in_stock is True
    assert products[0].product_url == "https://www.zeptonow.com/pn/amul-taaza/pvid/abc123"
    assert products[1].in_stock is False


def test_ids_are_stable_across_calls():
    raw = [{"text": "Milk\n1 L\n₹75\nAdd", "href": "/pn/x/pvid/1",
            "image": "", "addText": "Add"}]
    first = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    second = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    assert first[0].id == second[0].id


def test_cards_without_price_are_skipped():
    raw = [{"text": "Category banner", "href": "", "image": "", "addText": ""}]
    assert zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com") == []


def test_limit_is_respected():
    raw = [
        {"text": f"Milk {i}\n1 L\n₹{70 + i}\nAdd", "href": f"/pn/x/pvid/{i}",
         "image": "", "addText": "Add"}
        for i in range(10)
    ]
    assert len(zepto_products_from_raw("milk", raw, limit=3,
                                       base_url="https://www.zeptonow.com")) == 3
