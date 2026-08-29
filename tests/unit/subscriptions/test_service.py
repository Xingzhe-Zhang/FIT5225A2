from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.azure_api.subscriptions import repository as subscriptions_repository
from backend.azure_api.subscriptions import service as subscriptions_service
from backend.common.providers.fakes import FixedClock, SequenceIdGenerator


FIRST_ID = UUID("11111111-1111-4111-8111-111111111111")
SECOND_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def make_service():
    repository_type = getattr(subscriptions_repository, "InMemorySubscriptionRepository", None)
    service_type = getattr(subscriptions_service, "SubscriptionService", None)
    assert repository_type is not None, "InMemorySubscriptionRepository has not been implemented"
    assert service_type is not None, "SubscriptionService has not been implemented"
    repository = repository_type()
    service = service_type(
        repository=repository,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([FIRST_ID, SECOND_ID]),
    )
    return service, repository


def test_create_normalizes_email_and_tags_and_lists_only_owner_records() -> None:
    service, _ = make_service()

    owned = service.create(
        owner_sub="owner",
        email=" Researcher@Example.COM ",
        tags=[" Dingo ", "dingo", "Wombat"],
    )
    service.create(owner_sub="other-owner", email="other@example.com", tags=["dingo"])

    assert owned.subscription_id == FIRST_ID
    assert owned.owner_sub == "owner"
    assert owned.email == "researcher@example.com"
    assert owned.tags == ("dingo", "wombat")
    assert owned.status == "active"
    assert owned.version == 1
    assert service.list(owner_sub="owner") == [owned]


def test_update_replaces_fields_and_increments_version() -> None:
    service, _ = make_service()
    created = service.create(owner_sub="owner", email="old@example.com", tags=["dingo"])

    updated = service.update(
        owner_sub="owner",
        subscription_id=created.subscription_id,
        email="new@example.com",
        tags=["Cassowary", "cassowary"],
        expected_version=1,
    )

    assert updated.email == "new@example.com"
    assert updated.tags == ("cassowary",)
    assert updated.version == 2
    assert updated.updated_at == NOW


def test_stale_update_is_rejected_without_overwriting_current_record() -> None:
    service, repository = make_service()
    created = service.create(owner_sub="owner", email="old@example.com", tags=["dingo"])
    service.update(
        owner_sub="owner",
        subscription_id=created.subscription_id,
        email="new@example.com",
        tags=["wombat"],
        expected_version=1,
    )

    with pytest.raises(subscriptions_service.SubscriptionConflict):
        service.update(
            owner_sub="owner",
            subscription_id=created.subscription_id,
            email="stale@example.com",
            tags=["stale"],
            expected_version=1,
        )

    assert repository.get("owner", created.subscription_id).email == "new@example.com"


def test_update_and_delete_cannot_cross_owner_boundary() -> None:
    service, repository = make_service()
    created = service.create(owner_sub="owner", email="owner@example.com", tags=["dingo"])

    with pytest.raises(subscriptions_service.SubscriptionNotFound):
        service.update(
            owner_sub="other-owner",
            subscription_id=created.subscription_id,
            email="attacker@example.com",
            tags=["wombat"],
            expected_version=1,
        )
    assert service.delete(owner_sub="other-owner", subscription_id=created.subscription_id) is False
    assert repository.get("owner", created.subscription_id) == created


def test_delete_is_idempotent_and_removes_only_owned_subscription() -> None:
    service, _ = make_service()
    created = service.create(owner_sub="owner", email="owner@example.com", tags=["dingo"])

    assert service.delete(owner_sub="owner", subscription_id=created.subscription_id) is True
    assert service.delete(owner_sub="owner", subscription_id=created.subscription_id) is False
    assert service.list(owner_sub="owner") == []
