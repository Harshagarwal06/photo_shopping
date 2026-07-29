from pathlib import Path

from app.comparison_service import ComparisonService
from app.config import Settings
from app.models import (
    CartPlan,
    ComparisonOperation,
    ComparisonProposal,
    ComparisonReport,
    PlannedItem,
)
from app.state_store import SQLiteStateStore


def build_store(path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(
        path,
        record_ttl_seconds=3600,
        telemetry_retention_seconds=7200,
    )


def test_state_records_survive_new_store_instances(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = build_store(path)
    first.save("draft", "abc", {"safe": "payload"})

    second = build_store(path)

    assert second.load("draft", "abc") == {"safe": "payload"}


def test_expired_state_is_deleted_on_read(tmp_path, monkeypatch):
    path = tmp_path / "state.sqlite3"
    store = build_store(path)
    clock = [1000.0]
    monkeypatch.setattr("app.state_store.time.time", lambda: clock[0])
    store.save("draft", "abc", {"safe": True}, ttl_seconds=10)
    clock[0] += 11

    assert store.load("draft", "abc") is None


def test_metric_summary_contains_only_enumerated_operational_fields(tmp_path):
    store = build_store(tmp_path / "state.sqlite3")
    store.record_metric(
        "provider_search",
        stage="provider",
        provider="blinkit",
        outcome="ok",
        duration_ms=12.5,
        item_count=4,
    )

    summary = store.metric_summary()

    assert summary["groups"] == [
        {
            "event": "provider_search",
            "stage": "provider",
            "provider": "blinkit",
            "outcome": "ok",
            "count": 1,
            "item_count": 4,
            "average_duration_ms": 12.5,
            "p95_duration_ms": 12.5,
        }
    ]
    assert "query" not in repr(summary).casefold()
    assert "product" not in repr(summary).casefold()


def test_comparison_proposals_and_operations_recover_after_restart(tmp_path):
    store = build_store(tmp_path / "state.sqlite3")
    settings = Settings(_env_file=None)
    plan = CartPlan(items=[PlannedItem(search_term="milk")])
    proposal = ComparisonProposal(plan=plan, report=ComparisonReport())
    operation = ComparisonOperation(
        proposal_id=proposal.id,
        report=ComparisonReport(),
    )
    store.save(
        "comparison_proposal",
        proposal.id,
        proposal.model_dump(mode="json"),
    )
    store.save(
        "comparison_operation",
        operation.id,
        operation.model_dump(mode="json"),
    )

    restarted = ComparisonService({}, settings, build_store(store.path))

    assert restarted.get_proposal(proposal.id) == proposal
    assert restarted.get_operation(operation.id) == operation
