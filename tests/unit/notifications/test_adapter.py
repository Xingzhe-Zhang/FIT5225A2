from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest

import notification_adapter


EVENT = {
    "detail": {
        "schema_version": "1.0",
        "event_id": "11111111-1111-4111-8111-111111111111",
        "media_id": "22222222-2222-4222-8222-222222222222",
        "owner_sub": "owner",
        "tag_counts": {"dingo": 1},
        "model_version": "test",
        "occurred_at": "2026-08-28T00:00:00Z",
    }
}


@dataclass
class Outcome:
    subscription_id: UUID
    status: str


def test_eventbridge_adapter_validates_detail_and_reports_delivery(monkeypatch) -> None:
    class Evaluator:
        def evaluate(self, event: object) -> list[Outcome]:
            assert event.owner_sub == "owner"
            return [Outcome(UUID("33333333-3333-4333-8333-333333333333"), "sent")]

    monkeypatch.setattr(notification_adapter, "_evaluator", lambda: Evaluator())

    assert notification_adapter.handler(EVENT, None) == {"status": "ok", "sent": 1, "failed": 0}


def test_eventbridge_adapter_raises_so_failed_delivery_is_retried(monkeypatch) -> None:
    class Evaluator:
        def evaluate(self, event: object) -> list[Outcome]:
            return [Outcome(UUID("33333333-3333-4333-8333-333333333333"), "failed")]

    monkeypatch.setattr(notification_adapter, "_evaluator", lambda: Evaluator())

    with pytest.raises(RuntimeError, match="notification deliveries failed"):
        notification_adapter.handler(EVENT, None)
