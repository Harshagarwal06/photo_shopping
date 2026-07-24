from app.models import DraftCart


def test_processing_notices_are_separate_from_warning_flags():
    draft = DraftCart(items=[], notices=["Processed locally."])

    assert draft.notices == ["Processed locally."]
    assert draft.flags == []
