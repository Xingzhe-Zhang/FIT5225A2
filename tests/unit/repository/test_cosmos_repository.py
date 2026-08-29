from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from backend.azure_api.media.cosmos_repository import CosmosPagedMediaRepository
from backend.common.contracts.models import MediaRecord


class _NotFound(Exception):
    status_code = 404


class _Conflict(Exception):
    status_code = 409


class _PreconditionFailed(Exception):
    status_code = 412


class FakeCosmosContainer:
    """Small owner-partitioned Cosmos double for reservation lifecycle tests."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, object]] = {}
        self.deleted: list[tuple[str, str]] = []

    def read_item(self, *, item: str, partition_key: str) -> dict[str, object]:
        try:
            return self.documents[(partition_key, item)]
        except KeyError as error:
            raise _NotFound from error

    def create_item(self, document: dict[str, object]) -> None:
        key = (str(document["owner_sub"]), str(document["id"]))
        if key in self.documents:
            raise _Conflict
        self.documents[key] = dict(document)

    def upsert_item(self, document: dict[str, object]) -> None:
        key = (str(document["owner_sub"]), str(document["id"]))
        self.documents[key] = dict(document)

    def delete_item(self, *, item: str, partition_key: str, **kwargs: object) -> None:
        key = (partition_key, item)
        if key not in self.documents:
            raise _NotFound
        if "etag" in kwargs and kwargs["etag"] != self.documents[key].get("_etag"):
            raise _PreconditionFailed
        self.deleted.append(key)
        del self.documents[key]

    def query_items(self, *, partition_key: str, **_kwargs):
        return [
            document
            for (owner_sub, _), document in self.documents.items()
            if owner_sub == partition_key
        ]


def _media_record(media_id: UUID, *, owner_sub: str = "owner", sha256: str = "a" * 64) -> MediaRecord:
    now = datetime(2026, 8, 22, 4, 0, tzinfo=UTC)
    return MediaRecord(
        media_id=media_id,
        owner_sub=owner_sub,
        sha256=sha256,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{media_id}/camera.jpg",
        thumbnail_storage_uri=None,
        tag_counts={"dingo": 1},
        manual_tags=[],
        model_version="1.0.0",
        status="ready",
        created_at=now,
        updated_at=now,
    )


def test_fresh_orphan_reservation_is_not_reclaimed_by_second_request() -> None:
    container = FakeCosmosContainer()
    repository = CosmosPagedMediaRepository(container)
    checksum = "b" * 64
    first_id = UUID("11111111-1111-4111-8111-111111111111")
    second_id = UUID("22222222-2222-4222-8222-222222222222")

    first = repository.reserve_upload("owner", checksum, first_id)
    second = repository.reserve_upload("owner", checksum, second_id)

    assert first.created is True
    assert second.created is False
    assert second.media_id == first_id
    assert ("owner", f"reservation:{checksum}") not in container.deleted
    assert container.documents[("owner", f"reservation:{checksum}")]["media_id"] == str(first_id)


def test_expired_orphan_reservation_uses_explicit_server_expiry() -> None:
    container = FakeCosmosContainer()
    repository = CosmosPagedMediaRepository(container)
    checksum = "e" * 64
    old_id = UUID("11111111-1111-4111-8111-111111111111")
    replacement_id = UUID("22222222-2222-4222-8222-222222222222")
    repository.reserve_upload(
        "owner",
        checksum,
        old_id,
        datetime.now(UTC) - timedelta(seconds=1),
    )

    result = repository.reserve_upload(
        "owner",
        checksum,
        replacement_id,
        datetime.now(UTC) + timedelta(minutes=15),
    )

    assert result.created is True
    assert result.media_id == replacement_id
    assert ("owner", f"reservation:{checksum}") in container.deleted
    assert container.documents[("owner", f"reservation:{checksum}")]["expires_at"] is not None


def test_legacy_orphan_reservation_without_created_at_is_reclaimed() -> None:
    container = FakeCosmosContainer()
    repository = CosmosPagedMediaRepository(container)
    checksum = "c" * 64
    old_id = UUID("11111111-1111-4111-8111-111111111111")
    replacement_id = UUID("22222222-2222-4222-8222-222222222222")
    reservation_id = f"reservation:{checksum}"
    container.documents[("owner", reservation_id)] = {
        "id": reservation_id,
        "kind": "reservation",
        "owner_sub": "owner",
        "sha256": checksum,
        "media_id": str(old_id),
    }

    result = repository.reserve_upload("owner", checksum, replacement_id)

    assert result.created is True
    assert result.media_id == replacement_id
    assert container.deleted == [("owner", reservation_id)]
    assert container.documents[("owner", reservation_id)]["media_id"] == str(replacement_id)


def test_delete_media_releases_its_owner_and_checksum_reservation() -> None:
    container = FakeCosmosContainer()
    repository = CosmosPagedMediaRepository(container)
    media_id = UUID("11111111-1111-4111-8111-111111111111")
    checksum = "d" * 64
    reservation_id = f"reservation:{checksum}"
    repository.reserve_upload("owner", checksum, media_id)
    repository.upsert(_media_record(media_id, sha256=checksum))

    assert repository.delete("owner", media_id) is True
    assert ("owner", str(media_id)) in container.deleted
    assert ("owner", reservation_id) in container.deleted
    assert ("owner", reservation_id) not in container.documents


def test_reservation_delete_does_not_remove_replaced_etag_document() -> None:
    container = FakeCosmosContainer()
    repository = CosmosPagedMediaRepository(container)
    checksum = "f" * 64
    reservation_id = f"reservation:{checksum}"
    container.documents[("owner", reservation_id)] = {
        "id": reservation_id,
        "kind": "reservation",
        "owner_sub": "owner",
        "sha256": checksum,
        "media_id": "11111111-1111-4111-8111-111111111111",
        "_etag": "new-etag",
    }

    assert repository._delete_reservation("owner", reservation_id, etag="old-etag") is False
    assert ("owner", reservation_id) in container.documents
    assert container.deleted == []


def test_thumbnail_uri_with_media_id_uses_owner_partition_point_read() -> None:
    class PointReadOnlyContainer(FakeCosmosContainer):
        def query_items(self, **_kwargs):
            raise AssertionError("standard derived thumbnail should not require a query")

    container = PointReadOnlyContainer()
    repository = CosmosPagedMediaRepository(container)
    media_id = UUID("11111111-1111-4111-8111-111111111111")
    thumbnail_uri = f"s3://media/derived/{media_id}/{'a' * 64}/thumbnail.jpg"
    media = _media_record(media_id).model_copy(
        update={"thumbnail_storage_uri": thumbnail_uri}
    )
    repository.upsert(media)

    assert repository.find_by_storage_uri("owner", thumbnail_uri) == media
    assert repository.find_by_storage_uri("other-owner", thumbnail_uri) is None
