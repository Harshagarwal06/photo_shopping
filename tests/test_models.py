import pytest
from pydantic import ValidationError

from app.models import DraftCart, PlannedItem, SearchRequest


def test_processing_notices_are_separate_from_warning_flags():
    draft = DraftCart(items=[], notices=["Processed locally."])

    assert draft.notices == ["Processed locally."]
    assert draft.flags == []


def test_manual_search_fields_reject_whitespace_only_values():
    with pytest.raises(ValidationError):
        SearchRequest(draft_id="draft", planned_item_id="item", query="   ")


def test_manual_search_preserves_an_unchanged_brand_but_drops_a_stale_one():
    item = PlannedItem(search_term="soap", context="Rin")
    item.apply_manual_query("Rin soap")
    assert (item.search_term, item.context, item.provider_query) == (
        "soap",
        "Rin",
        "Rin soap",
    )

    item.apply_manual_query("Dove soap")
    assert (item.search_term, item.context, item.provider_query) == (
        "Dove soap",
        "",
        "Dove soap",
    )


def test_descriptive_context_is_not_sent_as_catalogue_query_text():
    item = PlannedItem(
        search_term="milk",
        context="regular dairy milk; prefer lowest total price",
    )

    assert item.provider_query == "milk"
