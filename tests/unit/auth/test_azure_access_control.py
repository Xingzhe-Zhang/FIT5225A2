from __future__ import annotations

import importlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.azure_api.media.repository import InMemoryPagedMediaRepository
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import MediaRecord
from backend.common.errors.models import ApiError


MEDIA_ID = UUID("11111111-1111-4111-8111-111111111111")


class OwnerVerifier:
    def verify(self, token: str) -> AuthContext:
        owners = {"owner-a-token": "owner-a", "owner-b-token": "owner-b"}
        try:
            return AuthContext(sub=owners[token])
        except KeyError as error:
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401) from error


def media_record() -> MediaRecord:
    now = datetime(2026, 8, 29, tzinfo=UTC)
    return MediaRecord(
        media_id=MEDIA_ID,
        owner_sub="owner-a",
        sha256="a" * 64,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri="s3://media/originals/camera.jpg",
        thumbnail_storage_uri="s3://media/derived/thumbnail.jpg",
        tag_counts={"dingo": 1},
        manual_tags=[],
        model_version="model-v1",
        status="ready",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def azure_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    module = importlib.import_module("backend.azure_api.function_app")
    repository = InMemoryPagedMediaRepository()
    repository.upsert(media_record())
    monkeypatch.setattr(module, "_build_runtime", lambda: (repository, OwnerVerifier()))
    return TestClient(module.create_data_api())


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/internal/query/tags"),
        ("post", "/internal/query/species"),
        ("post", "/internal/query/thumbnail"),
        ("get", f"/internal/media/{MEDIA_ID}"),
    ),
)
def test_azure_business_routes_reject_anonymous_requests(
    azure_client: TestClient,
    method: str,
    path: str,
) -> None:
    response = azure_client.request(method, path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_HEADER_MISSING"


def test_azure_business_routes_reject_invalid_tokens(azure_client: TestClient) -> None:
    response = azure_client.get(
        f"/internal/media/{MEDIA_ID}",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_azure_media_lookup_is_owner_partitioned(azure_client: TestClient) -> None:
    owner_a = azure_client.get(
        f"/internal/media/{MEDIA_ID}",
        headers={"Authorization": "Bearer owner-a-token"},
    )
    owner_b = azure_client.get(
        f"/internal/media/{MEDIA_ID}",
        headers={"Authorization": "Bearer owner-b-token"},
    )

    assert owner_a.status_code == 200
    assert owner_b.status_code == 404


def test_azure_anonymous_surface_is_minimal(azure_client: TestClient) -> None:
    assert azure_client.get("/health").json() == {"status": "ok"}
    assert azure_client.get("/openapi.json").status_code == 404
    assert azure_client.get("/docs").status_code == 404


def test_azure_health_reports_degraded_when_cosmos_query_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("backend.azure_api.function_app")

    class FailingRepository:
        def list_for_owner(self, owner_sub: str) -> list[MediaRecord]:
            assert owner_sub == "__azure_healthcheck__"
            raise RuntimeError("Cosmos unavailable")

    monkeypatch.setattr(module, "_build_runtime", lambda: (FailingRepository(), OwnerVerifier()))

    assert TestClient(module.create_data_api()).get("/health").json() == {"status": "degraded"}
