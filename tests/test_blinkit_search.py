import asyncio

import pytest

from app.blinkit import SEARCH_RETRY_DELAYS_MS, BlinkitClient, BlinkitError
from app.config import Settings


class FakePage:
    """Only the two calls BlinkitClient.search makes on a page directly."""

    def __init__(self):
        self.waits: list[float] = []

    async def wait_for_timeout(self, milliseconds: float) -> None:
        self.waits.append(milliseconds)


def build_client(scrapes: list[list[dict]]) -> tuple[BlinkitClient, list[str], FakePage]:
    """A client whose page work is replaced by a scripted list of scrape results."""
    client = BlinkitClient(Settings(demo_mode=False))
    page = FakePage()
    queries: list[str] = []

    async def fake_ensure_login(*, wait_for_user: bool = True) -> bool:
        return True

    async def fake_get_page():
        return page

    async def fake_history(_page):
        return {}

    async def fake_scrape(_page, query):
        queries.append(query)
        return scrapes.pop(0) if scrapes else []

    client.ensure_login = fake_ensure_login
    client._get_page = fake_get_page
    client._get_order_history = fake_history
    client._scrape_search = fake_scrape
    return client, queries, page


PRODUCT_ROW = [{"text": "Amul Butter 100 g\n₹62", "href": "", "image": None, "addText": "ADD"}]


def test_search_returns_on_the_first_successful_scrape():
    client, queries, page = build_client([PRODUCT_ROW])

    products = asyncio.run(client.search("butter"))

    assert len(products) == 1
    assert queries == ["butter"]
    # Only the pacing wait; no retry delay was needed.
    assert all(wait < SEARCH_RETRY_DELAYS_MS[1] for wait in page.waits)


def test_search_retries_an_empty_page_and_recovers():
    """Blinkit serves an empty shell when throttling; the products are still there."""
    client, queries, _ = build_client([[], PRODUCT_ROW])

    products = asyncio.run(client.search("atta"))

    assert len(products) == 1
    assert queries == ["atta", "atta"]


def test_search_reports_throttling_rather_than_claiming_no_products():
    """An empty result must never be reported as "this product does not exist".

    Blinkit answers even nonsense queries with fallback products, so an empty
    scrape means the request was refused. Reporting it as no-results would mark
    the item missing and, in a comparison, cost Blinkit the ranking unfairly.
    """
    client, queries, _ = build_client([])

    with pytest.raises(BlinkitError, match="rapid searching"):
        asyncio.run(client.search("dal"))

    assert len(queries) == len(SEARCH_RETRY_DELAYS_MS)
