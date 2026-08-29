"""Subscription repository boundary and deterministic fake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Subscription:
    subscription_id: UUID
    owner_sub: str
    email: str
    tags: tuple[str, ...]
    status: Literal["active", "pending_confirmation"]
    version: int
    created_at: datetime
    updated_at: datetime
    sns_subscription_arn: str | None = None


class SubscriptionRepository(Protocol):
    def create(self, subscription: Subscription) -> None: ...
    def get(self, owner_sub: str, subscription_id: UUID) -> Subscription | None: ...
    def list_for_owner(self, owner_sub: str) -> list[Subscription]: ...
    def replace(self, subscription: Subscription, *, expected_version: int) -> bool: ...
    def delete(self, owner_sub: str, subscription_id: UUID) -> bool: ...


class InMemorySubscriptionRepository:
    """Independent deterministic fake with owner partition and version CAS."""

    def __init__(self) -> None:
        self._subscriptions: dict[tuple[str, UUID], Subscription] = {}

    def create(self, subscription: Subscription) -> None:
        key = (subscription.owner_sub, subscription.subscription_id)
        if key in self._subscriptions:
            raise ValueError("subscription already exists")
        self._subscriptions[key] = subscription

    def get(self, owner_sub: str, subscription_id: UUID) -> Subscription | None:
        return self._subscriptions.get((owner_sub, subscription_id))

    def list_for_owner(self, owner_sub: str) -> list[Subscription]:
        return sorted(
            (
                subscription
                for (record_owner, _), subscription in self._subscriptions.items()
                if record_owner == owner_sub
            ),
            key=lambda subscription: (subscription.created_at, str(subscription.subscription_id)),
        )

    def replace(self, subscription: Subscription, *, expected_version: int) -> bool:
        key = (subscription.owner_sub, subscription.subscription_id)
        current = self._subscriptions.get(key)
        if current is None or current.version != expected_version:
            return False
        self._subscriptions[key] = subscription
        return True

    def delete(self, owner_sub: str, subscription_id: UUID) -> bool:
        return self._subscriptions.pop((owner_sub, subscription_id), None) is not None
