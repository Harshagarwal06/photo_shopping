from __future__ import annotations

import hashlib

from .models import Product

CATALOG: dict[str, list[tuple[str, str, float]]] = {
    "milk": [
        ("Amul Taaza Toned Fresh Milk", "1 L", 56),
        ("Mother Dairy Toned Milk", "1 L", 57),
        ("Amul Gold Full Cream Milk", "1 L", 68),
    ],
    "eggs": [
        ("Farm Made Free Range Eggs", "12 pcs", 148),
        ("Eggoz Nutrition Protein Rich Eggs", "12 pcs", 129),
        ("Fresh White Eggs", "6 pcs", 62),
    ],
    "dishwashing liquid": [
        ("Vim Lemon Dishwash Gel", "500 ml", 115),
        ("Pril Lime Dishwash Liquid", "425 ml", 99),
        ("Vim Dishwash Gel Pouch", "750 ml", 159),
    ],
    "coffee": [
        ("Nescafé Classic Instant Coffee", "45 g", 165),
        ("Bru Instant Coffee", "50 g", 155),
        ("Continental Xtra Instant Coffee", "50 g", 145),
    ],
    "rice": [
        ("India Gate Everyday Basmati Rice", "1 kg", 149),
        ("Daawat Rozana Gold Basmati Rice", "1 kg", 139),
        ("Fortune Basmati Rice", "1 kg", 128),
    ],
    "atta": [
        ("Aashirvaad Whole Wheat Atta", "1 kg", 68),
        ("Fortune Chakki Fresh Atta", "1 kg", 61),
        ("Pillsbury Chakki Fresh Atta", "1 kg", 65),
    ],
    "bread": [
        ("Harvest Gold White Bread", "400 g", 45),
        ("English Oven Brown Bread", "400 g", 55),
        ("Britannia 100% Whole Wheat Bread", "400 g", 60),
    ],
    "sugar": [
        ("Madhur Pure & Hygienic Sugar", "1 kg", 58),
        ("Dhampure White Crystal Sugar", "1 kg", 62),
        ("Trust Classic Sugar", "1 kg", 56),
    ],
}


def demo_search(query: str) -> list[Product]:
    normalized = query.casefold().strip()
    key = next(
        (name for name in CATALOG if name in normalized or normalized in name),
        None,
    )
    if key is None:
        # Unknown demo queries still return an explicitly synthetic, relevant
        # product so arbitrary user input can exercise review and comparison.
        # The stable price keeps screenshots and tests deterministic.
        digest = hashlib.sha1(normalized.encode()).digest()
        price = 40 + int.from_bytes(digest[:2], "big") % 161
        title = " ".join(word.capitalize() for word in query.split())
        rows = [(f"{title} · Demo product", "1 pack", float(price))]
    else:
        rows = CATALOG[key]
    products: list[Product] = []
    for index, (name, pack, price) in enumerate(rows):
        handle = f"demo:{query}:{index}"
        product_id = hashlib.sha1(handle.encode()).hexdigest()[:12]
        products.append(
            Product(
                id=product_id,
                name=name,
                pack_size=pack,
                price=price,
                image_url=None,
                in_stock=price > 0,
                handle=handle,
            )
        )
    return products
