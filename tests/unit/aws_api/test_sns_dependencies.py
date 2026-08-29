from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.common.config.settings import AppSettings
from backend.common.contracts.models import MediaRecord
from backend.common.providers.fakes import FixedClock, InMemoryMediaRepository, InMemoryObjectStorage, SequenceIdGenerator
from backend.azure_api.subscriptions.repository import InMemorySubscriptionRepository


NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
MEDIA_ID = UUID("11111111-1111-4111-8111-111111111111")
SUBSCRIPTION_ID = UUID("22222222-2222-4222-8222-222222222222")
EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
SHA = "b" * 64
URL = f"https://downloads.example.test/originals/{SHA}/camera.jpg"
TOPIC_ARN = "arn:aws:sns:ap-southeast-2:123456789012:pba-notifications"


class RecordingSnsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def publish(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return {"MessageId": "message-1"}

    def subscribe(self, **kwargs: object) -> dict[str, object]:
        return {"SubscriptionArn": f"{TOPIC_ARN}:subscription-1"}

    def get_subscription_attributes(self, **kwargs: object) -> dict[str, object]:
        return {"Attributes": {"SubscriptionArn": kwargs["SubscriptionArn"]}}

    def set_subscription_attributes(self, **kwargs: object) -> object:
        return {}

    def list_subscriptions_by_topic(self, **kwargs: object) -> dict[str, object]:
        return {"Subscriptions": []}

    def unsubscribe(self, **kwargs: object) -> object:
        return {}


def settings(topic: str | None = TOPIC_ARN) -> AppSettings:
    return AppSettings(app_env="development", notification_topic=topic, _env_file=None)


def media_record() -> MediaRecord:
    return MediaRecord(
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        sha256=SHA,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{SHA}/camera.jpg",
        thumbnail_storage_uri=None,
        tag_counts={"dingo": 1},
        manual_tags=[],
        model_version="speciesnet-1.0.0",
        status="ready",
        created_at=NOW,
        updated_at=NOW,
    )


def test_sns_factory_consumes_external_topic_and_delivers_matching_event() -> None:
    from backend.aws_api.dependencies import build_sns_feature_dependencies

    media = InMemoryMediaRepository()
    media.upsert(media_record())
    subscriptions = InMemorySubscriptionRepository()
    sns_client = RecordingSnsClient()
    dependencies = build_sns_feature_dependencies(
        settings=settings(),
        sns_client=sns_client,
        media_repository=media,
        storage=InMemoryObjectStorage(),
        subscription_repository=subscriptions,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([SUBSCRIPTION_ID, EVENT_ID]),
        download_base_url="https://downloads.example.test",
        bucket_name="media",
        application_base_url="https://app.example.test",
    )
    dependencies.subscriptions.create(
        owner_sub="owner-123",
        email="watcher@example.test",
        tags=["night"],
    )

    dependencies.bulk_tags.update(
        owner_sub="owner-123",
        urls=[URL],
        tags=["night"],
        operation=1,
    )

    assert len(sns_client.calls) == 1
    assert sns_client.calls[0]["TopicArn"] == TOPIC_ARN
    assert "AWS_ACCESS_KEY" not in repr(sns_client.calls[0]).upper()


def test_sns_factory_requires_notification_topic_configuration() -> None:
    from backend.aws_api.dependencies import build_sns_feature_dependencies

    with pytest.raises(ValueError, match="NOTIFICATION_TOPIC"):
        build_sns_feature_dependencies(
            settings=settings(None),
            sns_client=RecordingSnsClient(),
            media_repository=InMemoryMediaRepository(),
            storage=InMemoryObjectStorage(),
            subscription_repository=InMemorySubscriptionRepository(),
            clock=FixedClock(NOW),
            ids=SequenceIdGenerator([]),
            download_base_url="https://downloads.example.test",
            bucket_name="media",
            application_base_url="https://app.example.test",
        )
