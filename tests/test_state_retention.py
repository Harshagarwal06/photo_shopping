from app import main
from app.models import CartConstraints, DraftCart
from app.state_store import SQLiteStateStore


def test_draft_and_constraint_state_evict_together(monkeypatch, tmp_path):
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()
    monkeypatch.setattr(main.settings, "max_state_records", 10)
    monkeypatch.setattr(
        main,
        "state_store",
        SQLiteStateStore(
            tmp_path / "state.sqlite3",
            record_ttl_seconds=3600,
            telemetry_retention_seconds=3600,
        ),
    )

    for index in range(12):
        main.remember_draft(
            DraftCart(id=str(index), items=[]),
            CartConstraints(cart_budget=index + 1),
        )

    assert list(main.DRAFTS) == [str(index) for index in range(2, 12)]
    assert list(main.DRAFT_CONSTRAINTS) == list(main.DRAFTS)
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()


def test_draft_state_recovers_after_memory_is_cleared(monkeypatch, tmp_path):
    store = SQLiteStateStore(
        tmp_path / "state.sqlite3",
        record_ttl_seconds=3600,
        telemetry_retention_seconds=3600,
    )
    monkeypatch.setattr(main, "state_store", store)
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()
    draft = DraftCart(id="recover-me", items=[])
    constraints = CartConstraints(cart_budget=500)
    main.remember_draft(draft, constraints)
    main.DRAFTS.clear()
    main.DRAFT_CONSTRAINTS.clear()

    recovered = main.recalled_draft("recover-me")

    assert recovered == (draft, constraints)
