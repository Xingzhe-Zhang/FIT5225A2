from __future__ import annotations

import pytest

from backend.azure_api.media.repository import InMemoryPagedMediaRepository
from backend.azure_api.queries.service import (
    MediaNotFoundError,
    QueryService,
    ThumbnailUrlError,
    TrustedThumbnailNormalizer,
)
from tests.unit.repository.test_in_memory_repository import record


def service_with_records():
    repository = InMemoryPagedMediaRepository(page_size=1)
    values = [
        record(1, counts={"dingo": 2, "wombat": 1}),
        record(2, counts={"dingo": 3}, manual=["night"]),
        record(3, counts={"dingo": 4, "wombat": 2}),
        record(4, owner="owner-b", counts={"dingo": 8, "wombat": 8}),
    ]
    for value in values:
        repository.upsert(value)
    return (
        QueryService(
            repository,
            TrustedThumbnailNormalizer({"media.example.test": "media"}),
        ),
        values,
    )


def test_service_collects_every_tag_page_without_duplicates() -> None:
    service, values = service_with_records()

    results = service.query_tags("owner-a", {"dingo": 2, "wombat": 1})

    assert results == [values[0], values[2]]


def test_service_species_search_matches_manual_tags() -> None:
    service, values = service_with_records()
    assert service.query_species("owner-a", {"species": "NIGHT"}) == [values[1]]


def test_thumbnail_signed_url_maps_to_only_owner_original() -> None:
    service, values = service_with_records()

    result = service.query_thumbnail(
        "owner-a",
        {
            "thumbnail_url": (
                "https://media.example.test/derived/1/thumbnail.jpg"
                "?X-Amz-Signature=first&X-Amz-Expires=900"
            )
        },
    )

    assert result == values[0]
    with pytest.raises(MediaNotFoundError):
        service.query_thumbnail(
            "owner-b",
            {"thumbnail_url": "https://media.example.test/derived/1/thumbnail.jpg"},
        )


def test_thumbnail_signed_url_can_locate_an_owned_video() -> None:
    repository = InMemoryPagedMediaRepository(page_size=1)
    video = record(5, counts={"cat": 2}).model_copy(
        update={
            "file_name": "cat-cattle.mp4",
            "media_type": "video",
            "original_storage_uri": "s3://media/originals/5/cat-cattle.mp4",
            "thumbnail_storage_uri": "s3://media/derived/5/thumbnail.jpg",
        }
    )
    repository.upsert(video)
    service = QueryService(
        repository,
        TrustedThumbnailNormalizer({"media.example.test": "media"}),
    )

    result = service.query_thumbnail(
        "owner-a",
        {"thumbnail_url": "https://media.example.test/derived/5/thumbnail.jpg"},
    )

    assert result == video


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.test/derived/1/thumbnail.jpg",
        "https://foreign.example.test/derived/1/thumbnail.jpg",
        "https://media.example.test/originals/1/camera.jpg",
        "https://media.example.test/derived/../originals/1/camera.jpg",
    ],
)
def test_thumbnail_rejects_untrusted_or_non_thumbnail_urls(url: str) -> None:
    service, _ = service_with_records()
    with pytest.raises(ThumbnailUrlError):
        service.query_thumbnail("owner-a", {"thumbnail_url": url})
