"""Subscription CRUD service."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol
from uuid import UUID

from backend.azure_api.subscriptions.repository import Subscription, SubscriptionRepository
from backend.common.contracts.models import NotificationSubscription
from backend.common.providers.interfaces import Clock, IdGenerator


class SubscriptionManager(Protocol):
    def subscribe(self, email: str) -> tuple[str | None, str]: ...
    def unsubscribe(self, subscription_arn: str | None) -> None: ...
    def status(self, subscription_arn: str | None, email: str) -> tuple[str, str | None]: ...
    def status_check(self, subscription: Subscription) -> bool: ...


class SubscriptionNotFound(LookupError):
    """The requested subscription is absent from the authenticated owner partition."""


class SubscriptionConflict(RuntimeError):
    """The caller attempted to replace a stale subscription version."""


class SubscriptionService:
    def __init__(
        self,
        *,
        repository: SubscriptionRepository,
        clock: Clock,
        ids: IdGenerator,
        manager: SubscriptionManager | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids
        self._manager = manager

    def create(self, *, owner_sub: str, email: str, tags: list[str]) -> Subscription:
        normalized_email, normalized_tags = self._normalize(email=email, tags=tags)
        now = self._clock.now_utc()
        subscription_arn, status = (
            self._manager.subscribe(normalized_email)
            if self._manager is not None
            else (None, "active")
        )
        subscription = Subscription(
            subscription_id=self._ids.new_uuid(),
            owner_sub=owner_sub,
            email=normalized_email,
            tags=normalized_tags,
            status=status,  # type: ignore[arg-type]
            version=1,
            created_at=now,
            updated_at=now,
            sns_subscription_arn=subscription_arn,
        )
        try:
            self._repository.create(subscription)
        except Exception:
            if self._manager is not None:
                self._manager.unsubscribe(subscription_arn)
            raise
        return subscription

    def list(self, *, owner_sub: str) -> list[Subscription]:
        subscriptions = self._repository.list_for_owner(owner_sub)
        if self._manager is None:
            return subscriptions
        refreshed: list[Subscription] = []
        for subscription in subscriptions:
            status, arn = self._manager.status(subscription.sns_subscription_arn, subscription.email)
            if status == subscription.status and arn == subscription.sns_subscription_arn:
                refreshed.append(subscription)
                continue
            updated = replace(subscription, status=status, sns_subscription_arn=arn)
            if self._repository.replace(updated, expected_version=subscription.version):
                refreshed.append(updated)
            else:
                refreshed.append(subscription)
        return refreshed

    def update(
        self,
        *,
        owner_sub: str,
        subscription_id: UUID,
        email: str,
        tags: list[str],
        expected_version: int,
    ) -> Subscription:
        current = self._repository.get(owner_sub, subscription_id)
        if current is None:
            raise SubscriptionNotFound("subscription was not found")
        if current.version != expected_version:
            raise SubscriptionConflict("subscription version has changed")
        normalized_email, normalized_tags = self._normalize(email=email, tags=tags)
        subscription_arn = current.sns_subscription_arn
        status = current.status
        if self._manager is not None and normalized_email != current.email:
            self._manager.unsubscribe(current.sns_subscription_arn)
            subscription_arn, status = self._manager.subscribe(normalized_email)
        updated = Subscription(
            subscription_id=current.subscription_id,
            owner_sub=current.owner_sub,
            email=normalized_email,
            tags=normalized_tags,
            status=status,  # type: ignore[arg-type]
            version=expected_version + 1,
            created_at=current.created_at,
            updated_at=self._clock.now_utc(),
            sns_subscription_arn=subscription_arn,
        )
        if not self._repository.replace(updated, expected_version=expected_version):
            raise SubscriptionConflict("subscription version has changed")
        return updated

    def delete(self, *, owner_sub: str, subscription_id: UUID) -> bool:
        current = self._repository.get(owner_sub, subscription_id)
        if current is None:
            return False
        deleted = self._repository.delete(owner_sub, subscription_id)
        if deleted and self._manager is not None:
            self._manager.unsubscribe(current.sns_subscription_arn)
        return deleted

    @staticmethod
    def _normalize(*, email: str, tags: list[str]) -> tuple[str, tuple[str, ...]]:
        normalized_tags = tuple(sorted({tag.strip().casefold() for tag in tags if tag.strip()}))
        normalized_email = email.strip().casefold()
        validated = NotificationSubscription(email=normalized_email, tags=list(normalized_tags))
        return validated.email, tuple(validated.tags)
