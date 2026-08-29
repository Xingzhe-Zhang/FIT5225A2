"""Subscription matching and retry-safe delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol
from urllib.parse import urlencode
from uuid import UUID

from backend.azure_api.subscriptions.repository import Subscription, SubscriptionRepository
from backend.common.contracts.models import TaggingCompletedEvent
from backend.common.providers.interfaces import Notifier


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    subscription_id: UUID
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _EventData:
    event_id: UUID
    media_id: UUID
    owner_sub: str
    tags: frozenset[str]


class InMemoryDeliveryLedger:
    """Records only completed deliveries for deterministic retry deduplication."""

    def __init__(self) -> None:
        self._delivered: set[tuple[UUID, UUID]] = set()

    def contains(self, event_id: UUID, subscription_id: UUID) -> bool:
        return (event_id, subscription_id) in self._delivered

    def mark_delivered(self, event_id: UUID, subscription_id: UUID) -> None:
        self._delivered.add((event_id, subscription_id))


class DeliveryLedger(Protocol):
    def contains(self, event_id: UUID, subscription_id: UUID) -> bool: ...
    def mark_delivered(self, event_id: UUID, subscription_id: UUID) -> None: ...


class NotificationEvaluator:
    def __init__(
        self,
        *,
        repository: SubscriptionRepository,
        notifier: Notifier,
        ledger: DeliveryLedger,
        app_base_url: str,
        subscription_is_active: Callable[[Subscription], bool] | None = None,
    ) -> None:
        if not (app_base_url.startswith("https://") or app_base_url.startswith("http://localhost")):
            raise ValueError("app_base_url must use HTTPS outside localhost")
        self._repository = repository
        self._notifier = notifier
        self._ledger = ledger
        self._app_base_url = app_base_url.rstrip("/")
        self._subscription_is_active = subscription_is_active

    def evaluate(
        self,
        event: TaggingCompletedEvent | Mapping[str, object],
    ) -> list[DeliveryOutcome]:
        data = self._event_data(event)
        outcomes: list[DeliveryOutcome] = []
        for subscription in self._repository.list_for_owner(data.owner_sub):
            if self._subscription_is_active is not None:
                if not self._subscription_is_active(subscription):
                    continue
            elif subscription.status != "active":
                continue
            matched = sorted(set(subscription.tags) & set(data.tags))
            if not matched:
                continue
            if self._ledger.contains(data.event_id, subscription.subscription_id):
                outcomes.append(DeliveryOutcome(subscription.subscription_id, "deduplicated"))
                continue
            subject, body = self._message(media_id=data.media_id, matched_tags=matched)
            try:
                self._notifier.send(subscription.email, subject, body)
            except Exception as error:
                outcomes.append(
                    DeliveryOutcome(subscription.subscription_id, "failed", type(error).__name__)
                )
                continue
            self._ledger.mark_delivered(data.event_id, subscription.subscription_id)
            outcomes.append(DeliveryOutcome(subscription.subscription_id, "sent"))
        return outcomes

    def _message(self, *, media_id: UUID, matched_tags: list[str]) -> tuple[str, str]:
        species = ", ".join(matched_tags)
        subject = f"Pacific BioArchive: {species} detected"
        if len(subject) > 100:
            subject = f"Pacific BioArchive: {matched_tags[0][:70]} detected"
        link = f"{self._app_base_url}/library?{urlencode({'media_id': str(media_id)})}"
        body = (
            "A newly ready or updated media file matched your watched species.\n\n"
            f"Species: {species}\n"
            f"Open in Pacific BioArchive: {link}"
        )
        return subject, body

    @staticmethod
    def _event_data(event: TaggingCompletedEvent | Mapping[str, object]) -> _EventData:
        if isinstance(event, TaggingCompletedEvent):
            return _EventData(
                event_id=event.event_id,
                media_id=event.media_id,
                owner_sub=event.owner_sub,
                tags=frozenset(tag.strip().casefold() for tag in event.tag_counts if tag.strip()),
            )
        if event.get("event_type") != "manual_tags_updated":
            raise ValueError("unsupported notification event")
        raw_tags = event.get("tags")
        if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
            raise ValueError("manual tag event requires a string tag list")
        owner_sub = event.get("owner_sub")
        if not isinstance(owner_sub, str) or not owner_sub:
            raise ValueError("manual tag event requires owner_sub")
        return _EventData(
            event_id=UUID(str(event.get("event_id"))),
            media_id=UUID(str(event.get("media_id"))),
            owner_sub=owner_sub,
            tags=frozenset(tag.strip().casefold() for tag in raw_tags if tag.strip()),
        )
