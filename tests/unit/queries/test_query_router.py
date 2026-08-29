from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.common.auth.models import AuthContext
from backend.common.contracts.models import MediaRecord
from backend.common.errors.models import ApiError
from backend.common.providers.fakes import (
    DeterministicObjectUrlSigner,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
)
from backend.common.providers.interfaces import InferenceResult
from backend.azure_api.queries.service import MediaNotFoundError
from backend.temporary_query.service import TemporaryQueryService


JPEG = b"\xff\xd8\xff\xe0query-image"


def assert_not_running_on_event_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise AssertionError("synchronous query collaborator ran on the event-loop thread")


class StaticVerifier:
    _subjects = {
        "owner-a-token": "owner-a",
        "owner-b-token": "owner-b",
    }

    def verify(self, token: str) -> AuthContext:
        subject = self._subjects.get(token)
        if subject is None:
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401)
        return AuthContext(sub=subject)


class OwnerScopedQueryClient:
    def __init__(self, records: dict[str, MediaRecord]) -> None:
        self._records = records

    def query_tags(self, access_token: str, payload: object) -> list[MediaRecord]:
        assert_not_running_on_event_loop()
        return [self._record(access_token)]

    def query_species(self, access_token: str, payload: object) -> list[MediaRecord]:
        assert_not_running_on_event_loop()
        return [self._record(access_token)]

    def query_thumbnail(self, access_token: str, payload: object) -> MediaRecord:
        assert_not_running_on_event_loop()
        if "missing" in str(payload):
            raise MediaNotFoundError("thumbnail is not available")
        return self._record(access_token)

    def _record(self, access_token: str) -> MediaRecord:
        return self._records[StaticVerifier._subjects[access_token]]


class FixedInference:
    def infer(self, storage_uris: list[str]) -> InferenceResult:
        assert_not_running_on_event_loop()
        assert len(storage_uris) == 1
        return InferenceResult(tag_counts={"dingo": 1}, model_version="test-model")


def record(number: int, owner_sub: str) -> MediaRecord:
    now = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    media_id = UUID(f"00000000-0000-4000-8000-{number:012d}")
    return MediaRecord(
        media_id=media_id,
        owner_sub=owner_sub,
        sha256=f"{number:064x}",
        file_name=f"camera-{number}.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{number}/camera-{number}.jpg",
        thumbnail_storage_uri=f"s3://media/derived/{number}/thumbnail.jpg",
        tag_counts={"dingo": 1},
        manual_tags=[],
        model_version="test-model",
        status="ready",
        created_at=now,
        updated_at=now,
    )


def make_client() -> tuple[TestClient, InMemoryObjectStorage]:
    router_module = importlib.import_module("backend.aws_api.queries.router")
    gateway_module = importlib.import_module("backend.aws_api.queries.gateway")
    owner_a = record(1, "owner-a")
    owner_b = record(2, "owner-b")
    signer = DeterministicObjectUrlSigner(
        upload_base_url="https://uploads.example.test",
        download_base_url="https://downloads.example.test",
    )
    gateway = gateway_module.QueryGateway(
        client=OwnerScopedQueryClient({"owner-a": owner_a, "owner-b": owner_b}),
        signer=signer,
        storage_bucket="media",
    )
    repository = InMemoryMediaRepository()
    repository.upsert(owner_a)
    repository.upsert(owner_b)
    storage = InMemoryObjectStorage()
    temporary = TemporaryQueryService(
        storage=storage,
        repository=repository,
        inference=FixedInference(),
        signer=signer,
        bucket_name="media",
        max_bytes=1024,
    )
    dependencies = router_module.create_query_dependencies(
        gateway=gateway,
        temporary_service=temporary,
    )
    app = FastAPI()
    app.state.auth_verifier = StaticVerifier()

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", uuid4())
        return JSONResponse(
            status_code=error.status_code,
            content=error.to_response(request_id).model_dump(mode="json"),
        )

    app.include_router(router_module.create_query_router(dependencies))
    return TestClient(app), storage


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/queries/tags", {"dingo": 1}),
        ("/queries/species", {"species": "dingo"}),
        ("/queries/thumbnail", {"thumbnail_url": "https://media.example.test/derived/1/thumbnail.jpg"}),
    ],
)
def test_json_query_routes_return_only_the_authenticated_owners_result(
    path: str,
    payload: dict[str, object],
) -> None:
    client, _ = make_client()

    owner_a = client.post(path, json=payload, headers={"Authorization": "Bearer owner-a-token"})
    owner_b = client.post(path, json=payload, headers={"Authorization": "Bearer owner-b-token"})

    assert owner_a.status_code == 200
    assert owner_b.status_code == 200
    assert owner_a.json()["results"][0]["media_id"] == str(record(1, "owner-a").media_id)
    assert owner_b.json()["results"][0]["media_id"] == str(record(2, "owner-b").media_id)


def test_temporary_file_query_returns_only_the_authenticated_owners_result() -> None:
    client, _ = make_client()

    response = client.post(
        "/queries/by-file",
        files={"file": ("query.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["media_id"] == str(record(1, "owner-a").media_id)


@pytest.mark.parametrize(
    ("path", "request_kwargs"),
    [
        ("/queries/tags", {"json": {"dingo": 1}}),
        ("/queries/species", {"json": {"species": "dingo"}}),
        ("/queries/thumbnail", {"json": {"thumbnail_url": "https://media.example.test/derived/1/thumbnail.jpg"}}),
        ("/queries/by-file", {"files": {"file": ("query.jpg", JPEG, "image/jpeg")}}),
    ],
)
def test_query_routes_reject_missing_auth(path: str, request_kwargs: dict[str, object]) -> None:
    client, _ = make_client()

    response = client.post(path, **request_kwargs)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_HEADER_MISSING"


def test_malformed_temporary_upload_maps_to_api_error_and_cleans_up() -> None:
    client, storage = make_client()

    response = client.post(
        "/queries/by-file",
        files={"file": ("query.jpg", b"not-a-jpeg", "image/jpeg")},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUERY_FILE_INVALID"
    assert storage.list_keys("temporary-query/") == []


def test_invalid_json_query_uses_api_error_response() -> None:
    client, _ = make_client()

    response = client.post(
        "/queries/tags",
        json={},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "QUERY_VALIDATION_FAILED"


def test_missing_thumbnail_uses_not_found_api_error_response() -> None:
    client, _ = make_client()

    response = client.post(
        "/queries/thumbnail",
        json={"thumbnail_url": "https://media.example.test/derived/missing/thumbnail.jpg"},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "QUERY_NOT_FOUND"


def assert_query_file_api_error(response) -> None:
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "QUERY_FILE_INVALID"
    assert response.json()["error"]["message"]
    UUID(response.json()["error"]["request_id"])


def test_missing_file_uses_api_error_response_shape() -> None:
    client, storage = make_client()

    response = client.post(
        "/queries/by-file",
        data={"other": "value"},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert_query_file_api_error(response)
    assert storage.list_keys("temporary-query/") == []


def test_malformed_multipart_uses_api_error_response_shape() -> None:
    client, storage = make_client()

    response = client.post(
        "/queries/by-file",
        content=b"--wrong-boundary--\r\n",
        headers={
            "Authorization": "Bearer owner-a-token",
            "Content-Type": "multipart/form-data; boundary=expected-boundary",
        },
    )

    assert_query_file_api_error(response)
    assert storage.list_keys("temporary-query/") == []


def test_unexpected_multipart_field_uses_api_error_response_shape() -> None:
    client, storage = make_client()

    response = client.post(
        "/queries/by-file",
        data={"unexpected": "value"},
        files={"file": ("query.jpg", JPEG, "image/jpeg")},
        headers={"Authorization": "Bearer owner-a-token"},
    )

    assert_query_file_api_error(response)
    assert storage.list_keys("temporary-query/") == []
