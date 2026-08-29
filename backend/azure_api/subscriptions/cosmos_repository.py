"""Cosmos DB subscription repository using owner_sub as the partition key."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .repository import Subscription


class CosmosSubscriptionRepository:
    def __init__(self, container: Any) -> None:
        self._container = container

    def create(self, subscription: Subscription) -> None:
        self._container.create_item(_dump(subscription))

    def get(self, owner_sub: str, subscription_id: UUID) -> Subscription | None:
        try:
            item = self._container.read_item(item=str(subscription_id), partition_key=owner_sub)
        except Exception as error:
            if getattr(error, "status_code", None) == 404:
                return None
            raise
        return _load(item)

    def list_for_owner(self, owner_sub: str) -> list[Subscription]:
        items = self._container.query_items(query="SELECT * FROM c", partition_key=owner_sub)
        return sorted((_load(item) for item in items), key=lambda item: (item.created_at, str(item.subscription_id)))

    def replace(self, subscription: Subscription, *, expected_version: int) -> bool:
        current = self.get(subscription.owner_sub, subscription.subscription_id)
        if current is None or current.version != expected_version:
            return False
        self._container.replace_item(item=str(subscription.subscription_id), body=_dump(subscription))
        return True

    def delete(self, owner_sub: str, subscription_id: UUID) -> bool:
        try:
            self._container.delete_item(item=str(subscription_id), partition_key=owner_sub)
        except Exception as error:
            if getattr(error, "status_code", None) == 404:
                return False
            raise
        return True


def _dump(subscription: Subscription) -> dict[str, object]:
    return {
        "id": str(subscription.subscription_id),
        "owner_sub": subscription.owner_sub,
        "email": subscription.email,
        "tags": list(subscription.tags),
        "status": subscription.status,
        "version": subscription.version,
        "created_at": subscription.created_at.isoformat(),
        "updated_at": subscription.updated_at.isoformat(),
        "sns_subscription_arn": subscription.sns_subscription_arn,
    }


def _load(item: dict[str, object]) -> Subscription:
    from datetime import datetime

    return Subscription(
        subscription_id=UUID(str(item["id"])),
        owner_sub=str(item["owner_sub"]),
        email=str(item["email"]),
        tags=tuple(str(tag) for tag in item.get("tags", [])),
        status=str(item["status"]),  # type: ignore[arg-type]
        version=int(item["version"]),
        created_at=datetime.fromisoformat(str(item["created_at"])),
        updated_at=datetime.fromisoformat(str(item["updated_at"])),
        sns_subscription_arn=(
            str(item["sns_subscription_arn"])
            if item.get("sns_subscription_arn")
            else None
        ),
    )
