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
}


def demo_search(query: str) -> list[Product]:
    key = next((name for name in CATALOG if name in query.lower() or query.lower() in name), None)
    rows = CATALOG.get(key or "", [(f"No close demo match for {query}", "1 pack", 0)])
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

