from app.blinkit import _product_image_key, _products_from_raw
from app.models import Product


def test_current_div_card_markup_parses_product_details():
    raw = [
        {
            "text": "62% OFF\n20 MINS\nBoldfit Steel Wall Bottle\n1 pc\n₹299\n₹799\nADD",
            "href": "",
            "image": "https://cdn.grofers.com/cms/product/bottle.png",
            "addText": "ADD",
        },
        {
            "text": "20 MINS\nAquafina Packaged Water\n1 ltr\n₹20\nADD",
            "href": "",
            "image": "https://cdn.grofers.com/cms/product/water.png",
            "addText": "ADD",
        },
    ]

    products = _products_from_raw("water bottle", raw, limit=5)

    assert [product.name for product in products] == [
        "Boldfit Steel Wall Bottle",
        "Aquafina Packaged Water",
    ]
    assert products[0].pack_size == "1 pc"
    assert products[0].price == 299
    assert products[0].mrp == 799
    assert products[0].discount_percent == 62
    assert products[0].delivery_minutes == 20
    assert products[1].pack_size == "1 ltr"
    assert products[1].price == 20


def test_product_image_key_ignores_blinkit_resize_path():
    product = Product(
        id="tissue",
        name="Face Tissue",
        price=79,
        image_url=(
            "https://cdn.grofers.com/cdn-cgi/image/f=auto,w=270/"
            "da/cms-assets/cms/product/c899d704-b280-43f2-95d6-9edc8b8993a6.png"
        ),
        handle="fallback",
    )

    assert _product_image_key(product) == "c899d704-b280-43f2-95d6-9edc8b8993a6.png"
