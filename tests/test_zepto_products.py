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


def test_single_price_yields_no_mrp():
    """A card with exactly one price must have mrp set to None."""
    raw = [
        {
            "text": "Mother Dairy Milk\n500 ml\n₹42\nOut of stock",
            "href": "/pn/mother-dairy/pvid/def456",
            "image": "https://cdn.zeptonow.com/md.png",
            "addText": "Out of stock",
        },
    ]
    products = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    assert len(products) == 1
    assert products[0].price == 42.0
    assert products[0].mrp is None


def test_later_smaller_price_is_not_mrp():
    """A smaller later price must not be treated as MRP."""
    raw = [
        {
            "text": "Amul Milk\n1 L\n₹75\n₹50\nAdd",
            "href": "/pn/amul-milk/pvid/ghi789",
            "image": "https://cdn.zeptonow.com/amul.png",
            "addText": "Add",
        },
    ]
    products = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    assert len(products) == 1
    assert products[0].price == 75.0
    assert products[0].mrp is None


def test_missing_add_button_text_reads_as_in_stock():
    """A card with empty addText must yield in_stock=True."""
    raw = [
        {
            "text": "Organic Milk\n1 L\n₹90\nAdd",
            "href": "/pn/organic-milk/pvid/jkl012",
            "image": "https://cdn.zeptonow.com/organic.png",
            "addText": "",
        },
    ]
    products = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
    assert len(products) == 1
    assert products[0].in_stock is True


def test_out_of_stock_phrases_are_detected():
    """Each out-of-stock phrase ('Out of stock', 'Notify me', 'Sold out') must yield in_stock=False."""
    phrases = ["Out of stock", "Notify me", "Sold out"]
    for i, phrase in enumerate(phrases):
        raw = [
            {
                "text": f"Milk {i}\n1 L\n₹{70 + i}\n{phrase}",
                "href": f"/pn/milk-{i}/pvid/{i}",
                "image": "",
                "addText": phrase,
            },
        ]
        products = zepto_products_from_raw("milk", raw, limit=5, base_url="https://www.zeptonow.com")
        assert len(products) == 1, f"Failed for phrase: {phrase}"
        assert products[0].in_stock is False, f"Failed for phrase: {phrase}"


def test_structured_zepto_card_fields_override_button_text():
    raw = [
        {
            "text": "ADD\n₹24\nNandini Toned Fresh Milk | Pouch\n1 pack (500 ml)",
            "name": "Nandini Toned Fresh Milk | Pouch",
            "pack": "1 pack (500 ml)",
            "href": "/pn/nandini-milk/pvid/1",
            "image": "https://cdn.zeptonow.com/nandini.png",
            "addText": "ADD",
            "inStock": True,
        },
    ]

    products = zepto_products_from_raw(
        "milk",
        raw,
        limit=5,
        base_url="https://www.zepto.com",
    )

    assert products[0].name == "Nandini Toned Fresh Milk | Pouch"
    assert products[0].pack_size == "1 pack (500 ml)"
    assert products[0].price == 24
