from app import main
from app.models import CartConstraints, DraftCart


def test_draft_and_constraint_state_evict_together(monkeypatch):
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()
    monkeypatch.setattr(main.settings, "max_state_records", 10)

    for index in range(12):
        main.remember_draft(
            DraftCart(id=str(index), items=[]),
            CartConstraints(cart_budget=index + 1),
        )

    assert list(main.DRAFTS) == [str(index) for index in range(2, 12)]
    assert list(main.DRAFT_CONSTRAINTS) == list(main.DRAFTS)
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()
