from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.azure_api.management import service as management
from backend.common.contracts.models import MediaRecord
from backend.common.providers.fakes import (
    FixedClock,
    InMemoryMediaRepository,
    RecordingEventPublisher,
    SequenceIdGenerator,
)


MEDIA_ID = UUID("22222222-2222-4222-8222-222222222222")
EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
SHA = "a" * 64
NOW = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)
URL = f"https://downloads.example.test/originals/{SHA}/camera.jpg?signature=short-lived"


def make_record(*, owner_sub: str = "owner", updated_at: datetime | None = None) -> MediaRecord:
    created = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    return MediaRecord(
        media_id=MEDIA_ID,
        owner_sub=owner_sub,
        sha256=SHA,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{SHA}/camera.jpg",
        thumbnail_storage_uri=f"s3://media/derived/{SHA}/thumbnail.jpg",
        tag_counts={"dingo": 2},
        manual_tags=["existing"],
        model_version="speciesnet-1.0.0",
        status="ready",
        created_at=created,
        updated_at=updated_at or created,
    )


def make_service(repository: InMemoryMediaRepository):
    service_type = getattr(management, "BulkTagService", None)
    normalizer_type = getattr(management, "SignedUrlNormalizer", None)
    assert service_type is not None, "BulkTagService has not been implemented"
    assert normalizer_type is not None, "SignedUrlNormalizer has not been implemented"
    publisher = RecordingEventPublisher()
    service = service_type(
        repository=repository,
        publisher=publisher,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([EVENT_ID]),
        normalizer=normalizer_type(
            download_base_url="https://downloads.example.test",
            bucket_name="media",
        ),
    )
    return service, publisher


def test_bulk_add_normalizes_deduplicates_and_preserves_model_counts() -> None:
    repository = InMemoryMediaRepository()
    repository.upsert(make_record())
    service, publisher = make_service(repository)

    outcomes = service.update(
        owner_sub="owner",
        urls=[URL, URL],
        tags=[" Dingo ", "dingo", "Night"],
        operation=1,
    )

    assert [(item.status, item.media_id) for item in outcomes] == [("updated", MEDIA_ID)]
    updated = repository.get("owner", MEDIA_ID)
    assert updated is not None
    assert updated.manual_tags == ["dingo", "existing", "night"]
    assert updated.tag_counts == {"dingo": 2}
    assert updated.updated_at == NOW
    assert len(publisher.events) == 1
    assert publisher.events[0]["event_type"] == "manual_tags_updated"
    assert publisher.events[0]["tags"] == ["dingo", "existing", "night"]
    assert "s3://" not in str(publisher.events[0])


def test_repeated_add_and_missing_tag_removal_are_idempotent() -> None:
    repository = InMemoryMediaRepository()
    repository.upsert(make_record())
    add_service, publisher = make_service(repository)

    assert add_service.update(owner_sub="owner", urls=[URL], tags=["existing"], operation=1)[0].status == "unchanged"
    assert add_service.update(owner_sub="owner", urls=[URL], tags=["absent"], operation=0)[0].status == "unchanged"
    assert publisher.events == []


def test_bulk_update_is_owner_scoped() -> None:
    repository = InMemoryMediaRepository()
    repository.upsert(make_record(owner_sub="other-owner"))
    service, _ = make_service(repository)

    outcome = service.update(owner_sub="owner", urls=[URL], tags=["night"], operation=1)[0]

    assert outcome.status == "not_found"
    assert repository.get("other-owner", MEDIA_ID).manual_tags == ["existing"]


def test_optimistic_version_change_returns_conflict_without_overwrite() -> None:
    class ConcurrentRepository(InMemoryMediaRepository):
        def __init__(self) -> None:
            super().__init__()
            self._get_calls = 0

        def get(self, owner_sub: str, media_id: UUID):
            self._get_calls += 1
            current = super().get(owner_sub, media_id)
            if self._get_calls == 1 and current is not None:
                newer = current.model_copy(update={"updated_at": NOW})
                super().upsert(newer)
                return newer
            return current

    repository = ConcurrentRepository()
    repository.upsert(make_record())
    service, publisher = make_service(repository)

    outcome = service.update(owner_sub="owner", urls=[URL], tags=["new"], operation=1)[0]

    assert outcome.status == "conflict"
    assert repository.get("owner", MEDIA_ID).manual_tags == ["existing"]
    assert publisher.events == []
