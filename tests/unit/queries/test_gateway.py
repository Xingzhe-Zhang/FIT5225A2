from __future__ import annotations

import pytest

from backend.aws_api.queries.gateway import QueryGateway, StorageUriError
from backend.common.providers.fakes import DeterministicObjectUrlSigner
from tests.unit.repository.test_in_memory_repository import record


class FakeAzureQueryClient:
    def __init__(self, records) -> None:
        self.records = records
        self.calls: list[tuple[str, str, object]] = []

    def query_tags(self, access_token: str, payload: object):
        self.calls.append(("tags", access_token, payload))
        return self.records

    def query_species(self, access_token: str, payload: object):
        self.calls.append(("species", access_token, payload))
        return self.records

    def query_thumbnail(self, access_token: str, payload: object):
        self.calls.append(("thumbnail", access_token, payload))
        return self.records[0]


def gateway_for(records):
    client = FakeAzureQueryClient(records)
    signer = DeterministicObjectUrlSigner(
        upload_base_url="https://unused.example.test",
        download_base_url="https://signed.example.test",
    )
    return QueryGateway(
        client=client,
        signer=signer,
        storage_bucket="media",
        expires_in_seconds=300,
    ), client


def test_gateway_forwards_token_signs_results_and_removes_duplicates() -> None:
    image = record(1, counts={"dingo": 2})
    video = record(2, counts={"dingo": 1}).model_copy(
        update={
            "file_name": "clip.mp4",
            "media_type": "video",
            "original_storage_uri": "s3://media/originals/2/clip.mp4",
            "thumbnail_storage_uri": "s3://media/derived/2/poster.jpg",
        }
    )
    gateway, client = gateway_for([image, image, video])

    response = gateway.query_tags("cognito-access-token", {"dingo": 1})

    assert client.calls == [("tags", "cognito-access-token", {"dingo": 1})]
    assert [result.media_id for result in response.results] == [image.media_id, video.media_id]
    assert str(response.results[0].thumbnail_url) == (
        "https://signed.example.test/derived/1/thumbnail.jpg"
    )
    assert str(response.results[0].original_url) == (
        "https://signed.example.test/originals/1/camera%201.jpg"
    )
    assert str(response.results[1].thumbnail_url) == (
        "https://signed.example.test/derived/2/poster.jpg"
    )
    assert str(response.results[1].original_url) == (
        "https://signed.example.test/originals/2/clip.mp4"
    )
    serialized = response.model_dump_json()
    assert "cognito-access-token" not in serialized
    assert "s3://" not in serialized


def test_species_and_thumbnail_routes_use_same_signing_boundary() -> None:
    item = record(1, counts={"dingo": 1})
    gateway, client = gateway_for([item])

    species = gateway.query_species("token", {"species": "dingo"})
    thumbnail = gateway.query_thumbnail(
        "token", {"thumbnail_url": "https://media.example.test/derived/1/thumbnail.jpg"}
    )

    assert species.results[0].media_id == item.media_id
    assert thumbnail.results[0].media_id == item.media_id
    assert [call[0] for call in client.calls] == ["species", "thumbnail"]


def test_video_thumbnail_route_returns_signed_thumbnail_and_original_urls() -> None:
    video = record(2, counts={"cat": 2}).model_copy(
        update={
            "file_name": "cat-cattle.mp4",
            "media_type": "video",
            "original_storage_uri": "s3://media/originals/2/cat-cattle.mp4",
            "thumbnail_storage_uri": "s3://media/derived/2/thumbnail.jpg",
        }
    )
    gateway, _ = gateway_for([video])

    response = gateway.query_thumbnail(
        "token",
        {"thumbnail_url": "https://media.example.test/derived/2/thumbnail.jpg"},
    )

    assert str(response.results[0].thumbnail_url) == (
        "https://signed.example.test/derived/2/thumbnail.jpg"
    )
    assert str(response.results[0].original_url) == (
        "https://signed.example.test/originals/2/cat-cattle.mp4"
    )


def test_gateway_rejects_foreign_canonical_storage_bucket() -> None:
    foreign = record(1).model_copy(
        update={
            "original_storage_uri": "s3://other-bucket/originals/1/camera.jpg",
        }
    )
    gateway, _ = gateway_for([foreign])

    with pytest.raises(StorageUriError, match="configured bucket"):
        gateway.query_tags("token", {"dingo": 1})
