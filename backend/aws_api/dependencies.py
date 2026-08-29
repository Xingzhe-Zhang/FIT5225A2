"""Composition root inputs for the management and notification feature routers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.aws_api.management.deletion import CrossCloudDeleteService, DeletionOperationStore, InMemoryDeletionOperationStore
from backend.azure_api.management.service import BulkTagService, SignedUrlNormalizer
from backend.azure_api.subscriptions.repository import SubscriptionRepository
from backend.azure_api.subscriptions.service import SubscriptionManager, SubscriptionService
from backend.common.providers.interfaces import Clock, EventPublisher, IdGenerator, MediaRepository, Notifier, ObjectStorage
from backend.common.config.settings import AppSettings
from backend.notification_bridge.evaluator import DeliveryLedger, InMemoryDeliveryLedger, NotificationEvaluator
from backend.notification_bridge.sns import SnsNotifier, SnsPublishClient
from backend.notification_bridge.subscriptions import SnsEmailSubscriptionManager, SnsSubscriptionClient


class SnsFeatureClient(SnsPublishClient, SnsSubscriptionClient, Protocol):
    pass


class NotificationEventPublisher(EventPublisher):
    """Delivers supported media events through the configured notifier boundary."""

    def __init__(self, evaluator: NotificationEvaluator) -> None:
        self._evaluator = evaluator

    def publish(self, event: object) -> None:
        self._evaluator.evaluate(event)  # type: ignore[arg-type]


class StorageUrlNormalizer(Protocol):
    def canonical_storage_uri(self, url: str) -> str: ...


@dataclass(frozen=True, slots=True)
class FeatureDependencies:
    bulk_tags: BulkTagService
    deletion: CrossCloudDeleteService
    subscriptions: SubscriptionService


def build_feature_dependencies(
    *,
    media_repository: MediaRepository,
    storage: ObjectStorage,
    subscription_repository: SubscriptionRepository,
    notifier: Notifier,
    clock: Clock,
    ids: IdGenerator,
    download_base_url: str,
    bucket_name: str,
    application_base_url: str,
    url_normalizer: StorageUrlNormalizer | None = None,
    ledger: DeliveryLedger | None = None,
    operations: DeletionOperationStore | None = None,
    subscription_manager: SubscriptionManager | None = None,
) -> FeatureDependencies:
    """Build owner-scoped services with notification evaluation on tag events."""

    normalizer = url_normalizer or SignedUrlNormalizer(
        download_base_url=download_base_url,
        bucket_name=bucket_name,
    )
    evaluator = NotificationEvaluator(
        repository=subscription_repository,
        notifier=notifier,
        ledger=ledger or InMemoryDeliveryLedger(),
        app_base_url=application_base_url,
        subscription_is_active=(subscription_manager.status_check if subscription_manager is not None else None),
    )
    publisher = NotificationEventPublisher(evaluator)
    return FeatureDependencies(
        bulk_tags=BulkTagService(
            repository=media_repository,
            publisher=publisher,
            clock=clock,
            ids=ids,
            normalizer=normalizer,
        ),
        deletion=CrossCloudDeleteService(
            repository=media_repository,
            storage=storage,
            operations=operations or InMemoryDeletionOperationStore(),
            clock=clock,
            ids=ids,
            normalizer=normalizer,
        ),
        subscriptions=SubscriptionService(
            repository=subscription_repository,
            clock=clock,
            ids=ids,
            manager=subscription_manager,
        ),
    )


def build_sns_feature_dependencies(
    *,
    settings: AppSettings,
    sns_client: SnsFeatureClient,
    media_repository: MediaRepository,
    storage: ObjectStorage,
    subscription_repository: SubscriptionRepository,
    clock: Clock,
    ids: IdGenerator,
    download_base_url: str,
    bucket_name: str,
    application_base_url: str,
    url_normalizer: StorageUrlNormalizer | None = None,
    ledger: DeliveryLedger | None = None,
    operations: DeletionOperationStore | None = None,
) -> FeatureDependencies:
    """Compose feature services with an IAM-authenticated SNS-compatible client."""

    topic_arn = settings.notification_topic
    if topic_arn is None:
        raise ValueError("NOTIFICATION_TOPIC must configure the SNS topic ARN")
    manager = SnsEmailSubscriptionManager(client=sns_client, topic_arn=topic_arn)
    return build_feature_dependencies(
        media_repository=media_repository,
        storage=storage,
        subscription_repository=subscription_repository,
        notifier=SnsNotifier(client=sns_client, topic_arn=topic_arn),
        clock=clock,
        ids=ids,
        download_base_url=download_base_url,
        bucket_name=bucket_name,
        application_base_url=application_base_url,
        url_normalizer=url_normalizer,
        ledger=ledger,
        operations=operations,
        subscription_manager=manager,
    )
