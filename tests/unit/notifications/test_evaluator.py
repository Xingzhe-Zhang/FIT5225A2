from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.azure_api.subscriptions.repository import (
    InMemorySubscriptionRepository,
    Subscription,
)
from backend.common.contracts.models import TaggingCompletedEvent
from backend.common.providers.fakes import RecordingNotifier
from backend.notification_bridge import evaluator as notification_evaluator


EVENT_ID = UUID("11111111-1111-4111-8111-111111111111")
MEDIA_ID = UUID("22222222-2222-4222-8222-222222222222")
SUBSCRIPTION_ID = UUID("33333333-3333-4333-8333-333333333333")
NOW = datetime(2026, 8, 22, 12, 30, tzinfo=UTC)


def subscription(
    *,
    subscription_id: UUID = SUBSCRIPTION_ID,
    owner_sub: str = "owner",
    email: str = "researcher@example.com",
    tags: tuple[str, ...] = ("dingo",),
) -> Subscription:
    return Subscription(
        subscription_id=subscription_id,
        owner_sub=owner_sub,
        email=email,
        tags=tags,
        status="active",
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def tagging_event() -> TaggingCompletedEvent:
    return TaggingCompletedEvent(
        schema_version="1.0",
        event_id=EVENT_ID,
        media_id=MEDIA_ID,
        owner_sub="owner",
        tag_counts={"Dingo": 2, "wombat": 1},
        model_version="speciesnet-1.0.0",
        occurred_at=NOW,
    )


def make_evaluator(repository, notifier):
    evaluator_type = getattr(notification_evaluator, "NotificationEvaluator", None)
    ledger_type = getattr(notification_evaluator, "InMemoryDeliveryLedger", None)
    assert evaluator_type is not None, "NotificationEvaluator has not been implemented"
    assert ledger_type is not None, "InMemoryDeliveryLedger has not been implemented"
    return evaluator_type(
        repository=repository,
        notifier=notifier,
        ledger=ledger_type(),
        app_base_url="https://app.example.test",
    )


def test_tagging_event_matches_any_watched_tag_within_owner_partition() -> None:
    repository = InMemorySubscriptionRepository()
    repository.create(subscription())
    repository.create(
        subscription(
            subscription_id=UUID("44444444-4444-4444-8444-444444444444"),
            tags=("cassowary",),
        )
    )
    repository.create(
        subscription(
            subscription_id=UUID("55555555-5555-4555-8555-555555555555"),
            owner_sub="other-owner",
            email="other@example.com",
        )
    )
    notifier = RecordingNotifier()
    evaluator = make_evaluator(repository, notifier)

    outcomes = evaluator.evaluate(tagging_event())

    assert [(outcome.subscription_id, outcome.status) for outcome in outcomes] == [
        (SUBSCRIPTION_ID, "sent")
    ]
    assert len(notifier.messages) == 1
    message = notifier.messages[0]
    assert message.recipient == "researcher@example.com"
    assert "dingo" in message.subject.casefold()
    assert "https://app.example.test/library?media_id=22222222-2222-4222-8222-222222222222" in message.body


def test_successful_delivery_is_deduplicated_on_event_retry() -> None:
    repository = InMemorySubscriptionRepository()
    repository.create(subscription())
    notifier = RecordingNotifier()
    evaluator = make_evaluator(repository, notifier)

    first = evaluator.evaluate(tagging_event())
    second = evaluator.evaluate(tagging_event())

    assert first[0].status == "sent"
    assert second[0].status == "deduplicated"
    assert len(notifier.messages) == 1


def test_failed_delivery_is_not_marked_and_retry_can_succeed() -> None:
    class FailOnceNotifier(RecordingNotifier):
        def __init__(self) -> None:
            super().__init__()
            self.fail = True

        def send(self, recipient: str, subject: str, body: str) -> None:
            if self.fail:
                self.fail = False
                raise TimeoutError("provider timeout")
            super().send(recipient, subject, body)

    repository = InMemorySubscriptionRepository()
    repository.create(subscription())
    notifier = FailOnceNotifier()
    evaluator = make_evaluator(repository, notifier)

    first = evaluator.evaluate(tagging_event())
    second = evaluator.evaluate(tagging_event())
    third = evaluator.evaluate(tagging_event())

    assert first[0].status == "failed"
    assert second[0].status == "sent"
    assert third[0].status == "deduplicated"
    assert len(notifier.messages) == 1


def test_manual_tag_event_matches_and_never_copies_private_values_to_message() -> None:
    repository = InMemorySubscriptionRepository()
    repository.create(subscription(tags=("night",)))
    notifier = RecordingNotifier()
    evaluator = make_evaluator(repository, notifier)
    event = {
        "event_type": "manual_tags_updated",
        "event_id": str(EVENT_ID),
        "media_id": str(MEDIA_ID),
        "owner_sub": "owner",
        "tags": ["Night", "Dingo"],
        "storage_uri": "s3://private/original.jpg",
        "token": "secret-token",
    }

    outcome = evaluator.evaluate(event)[0]

    assert outcome.status == "sent"
    rendered = f"{notifier.messages[0].subject}\n{notifier.messages[0].body}"
    assert "night" in rendered.casefold()
    assert "s3://" not in rendered
    assert "secret-token" not in rendered


def test_active_subscription_is_rechecked_when_sns_manager_is_configured() -> None:
    repository = InMemorySubscriptionRepository()
    repository.create(subscription(tags=("night",)))
    notifier = RecordingNotifier()
    evaluator = notification_evaluator.NotificationEvaluator(
        repository=repository,
        notifier=notifier,
        ledger=notification_evaluator.InMemoryDeliveryLedger(),
        app_base_url="https://app.example.test",
        subscription_is_active=lambda _: False,
    )

    assert evaluator.evaluate(tagging_event()) == []
    assert notifier.messages == []
