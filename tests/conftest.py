import pytest

from app.search_cache import SEARCH_CACHE


@pytest.fixture(autouse=True)
def clear_search_cache():
    """The provider search cache is process-wide, so tests must not share it.

    Fake providers reuse real provider ids, and a cached result from one test
    would otherwise answer a later test's search — including one whose provider
    is meant to be failing.
    """
    SEARCH_CACHE.clear()
    yield
    SEARCH_CACHE.clear()
