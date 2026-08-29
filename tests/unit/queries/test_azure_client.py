from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from backend.aws_api.queries.azure_client import AzureDataApiClient
from backend.common.errors.models import ApiError


def media_payload() -> dict[str, object]:
    now = datetime(2026, 8, 27, tzinfo=UTC).isoformat()
    return {
        "media_id": str(UUID(int=1)),
        "owner_sub": "owner-a",
        "sha256": "a" * 64,
        "file_name": "kangaroo.jpg",
        "media_type": "image",
        "original_storage_uri": "s3://media/originals/1/kangaroo.jpg",
        "thumbnail_storage_uri": "s3://media/derived/1/thumbnail.jpg",
        "tag_counts": {"kangaroo": 1},
        "manual_tags": [],
        "model_version": "test",
        "status": "ready",
        "created_at": now,
        "updated_at": now,
    }


def test_species_query_forwards_bearer_token_and_validates_records() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/query/species"
        assert request.headers["authorization"] == "Bearer cognito-token"
        return httpx.Response(200, json=[media_payload()])

    client = AzureDataApiClient(
        "https://data.example.test/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    records = client.query_species("cognito-token", {"species": "kangaroo"})

    assert records[0].owner_sub == "owner-a"
    assert records[0].tag_counts == {"kangaroo": 1}


def test_rejected_token_is_mapped_without_exposing_the_token() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "rejected"}))
    client = AzureDataApiClient("https://data.example.test", client=httpx.Client(transport=transport))

    with pytest.raises(ApiError) as captured:
        client.query_tags("secret-token", {"kangaroo": 1})

    assert captured.value.status_code == 401
    assert "secret-token" not in str(captured.value)


def test_invalid_azure_payload_is_a_bad_gateway_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"not": "a list"}))
    client = AzureDataApiClient("https://data.example.test", client=httpx.Client(transport=transport))

    with pytest.raises(ApiError) as captured:
        client.query_species("token", {"species": "kangaroo"})

    assert captured.value.status_code == 502
