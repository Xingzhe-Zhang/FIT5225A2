from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.azure_api.media.repository import InMemoryPagedMediaRepository
from backend.common.contracts.models import MediaRecord


def record(
    number: int,
    *,
    owner: str = "owner-a",
    counts: dict[str, int] | None = None,
    manual: list[str] | None = None,
) -> MediaRecord:
    media_id = UUID(f"00000000-0000-4000-8000-{number:012d}")
    now = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
    return MediaRecord(
        media_id=media_id,
        owner_sub=owner,
        sha256=f"{number:064x}",
        file_name=f"camera-{number}.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{number}/camera {number}.jpg",
        thumbnail_storage_uri=f"s3://media/derived/{number}/thumbnail.jpg",
        tag_counts=counts or {},
        manual_tags=manual or [],
        model_version="1.0.0",
        status="ready",
        created_at=now,
        updated_at=now,
    )


def test_tag_query_is_owner_scoped_logical_and_with_minimum_counts() -> None:
    repository = InMemoryPagedMediaRepository(page_size=2)
    matching = record(1, counts={"dingo": 2, "wombat": 1})
    missing_count = record(2, counts={"dingo": 1, "wombat": 5})
    missing_species = record(3, counts={"dingo": 4})
    foreign = record(4, owner="owner-b", counts={"dingo": 9, "wombat": 9})
    for item in (matching, missing_count, missing_species, foreign):
        repository.upsert(item)

    assert repository.query_by_tags("owner-a", {"dingo": 2, "wombat": 1}) == [
        matching
    ]


def test_tag_query_matches_model_species_names_case_insensitively() -> None:
    repository = InMemoryPagedMediaRepository()
    matching = record(1, counts={"Bos_taurus": 2})
    repository.upsert(matching)

    assert repository.query_by_tags("owner-a", {"bos_taurus": 2}) == [matching]


def test_tag_query_matches_manual_tags_as_single_occurrences() -> None:
    repository = InMemoryPagedMediaRepository()
    manual = record(1, manual=["Night"])
    repository.upsert(manual)

    assert repository.query_by_tags("owner-a", {"night": 1}) == [manual]
    assert repository.query_by_tags("owner-a", {"night": 2}) == []


def test_species_query_includes_automatic_and_manual_tags_case_insensitively() -> None:
    repository = InMemoryPagedMediaRepository()
    automatic = record(1, counts={"Dingo": 1})
    manual = record(2, manual=["dingo"])
    unrelated = record(3, counts={"wombat": 1}, manual=["night"])
    for item in (automatic, manual, unrelated):
        repository.upsert(item)

    assert repository.query_by_species("owner-a", "DINGO") == [automatic, manual]


def test_paged_queries_return_stable_non_overlapping_pages() -> None:
    repository = InMemoryPagedMediaRepository(page_size=2)
    for number in range(1, 6):
        repository.upsert(record(number, counts={"dingo": 1}))

    first = repository.query_tags_page("owner-a", {"dingo": 1})
    second = repository.query_tags_page(
        "owner-a", {"dingo": 1}, continuation_token=first.continuation_token
    )
    third = repository.query_tags_page(
        "owner-a", {"dingo": 1}, continuation_token=second.continuation_token
    )

    ids = [item.media_id for page in (first, second, third) for item in page.records]
    assert ids == sorted(set(ids))
    assert len(ids) == 5
    assert third.continuation_token is None
