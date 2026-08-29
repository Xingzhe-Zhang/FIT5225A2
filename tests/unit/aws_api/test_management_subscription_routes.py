from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.azure_api.subscriptions.repository import InMemorySubscriptionRepository
from backend.common.auth.models import AuthContext
from backend.common.contracts.models import MediaRecord
from backend.common.errors.models import ApiError
from backend.common.providers.fakes import (
    FixedClock,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
    RecordingNotifier,
    SequenceIdGenerator,
)


MEDIA_ID = UUID("11111111-1111-4111-8111-111111111111")
TAG_EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
DELETE_OPERATION_ID = UUID("33333333-3333-4333-8333-333333333333")
SUBSCRIPTION_ID = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
SHA = "a" * 64
URL = f"https://downloads.example.test/originals/{SHA}/camera.jpg?signature=short-lived"


class StaticVerifier:
    def verify(self, token: str) -> AuthContext:
        if token != "valid-token":
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401)
        return AuthContext(sub="owner-123")


def make_record() -> MediaRecord:
    return MediaRecord(
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        sha256=SHA,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{SHA}/camera.jpg",
        thumbnail_storage_uri=None,
        tag_counts={"dingo": 2},
        manual_tags=[],
        model_version="speciesnet-1.0.0",
        status="ready",
        created_at=NOW,
        updated_at=NOW,
    )


def client_with_feature_routes() -> tuple[TestClient, RecordingNotifier]:
    from backend.aws_api.dependencies import build_feature_dependencies
    from backend.aws_api.management.router import create_management_router
    from backend.aws_api.subscriptions.router import create_subscription_router

    media = InMemoryMediaRepository()
    media.upsert(make_record())
    notifier = RecordingNotifier()
    dependencies = build_feature_dependencies(
        media_repository=media,
        storage=InMemoryObjectStorage(),
        subscription_repository=InMemorySubscriptionRepository(),
        notifier=notifier,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([TAG_EVENT_ID, DELETE_OPERATION_ID, SUBSCRIPTION_ID]),
        download_base_url="https://downloads.example.test",
        bucket_name="media",
        application_base_url="https://app.example.test",
    )
    app = FastAPI()
    app.state.auth_verifier = StaticVerifier()

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    app.include_router(create_management_router(dependencies))
    app.include_router(create_subscription_router(dependencies))
    return TestClient(app), notifier


def test_management_routes_are_authenticated_owner_scoped_and_publish_notifications() -> None:
    client, notifier = client_with_feature_routes()
    tag_payload = {"urls": [URL], "tags": ["Dingo"], "operation": 1}

    assert client.post("/media/tags", json=tag_payload).status_code == 401
    tagged = client.post("/media/tags", json=tag_payload, headers={"Authorization": "Bearer valid-token"})

    assert tagged.status_code == 200
    assert tagged.json()["results"][0]["status"] == "updated"
    assert notifier.messages == []

    created = client.post(
        "/subscriptions",
        json={"email": "watcher@example.test", "tags": ["dingo"]},
        headers={"Authorization": "Bearer valid-token"},
    )
    assert created.status_code == 201

    tagged_again = client.post(
        "/media/tags",
        json={"urls": [URL], "tags": ["night"], "operation": 1},
        headers={"Authorization": "Bearer valid-token"},
    )
    assert tagged_again.status_code == 200
    assert [(message.recipient, message.subject) for message in notifier.messages] == [
        ("watcher@example.test", "Pacific BioArchive: dingo detected")
    ]


def test_subscription_routes_provide_authenticated_list_update_delete_and_typed_errors() -> None:
    client, _ = client_with_feature_routes()
    headers = {"Authorization": "Bearer valid-token"}

    assert client.get("/subscriptions").status_code == 401
    assert client.get("/subscriptions", headers=headers).json() == {"results": []}

    assert client.put(
        f"/subscriptions/{SUBSCRIPTION_ID}",
        json={"email": "missing@example.test", "tags": ["dingo"], "expected_version": 1},
        headers=headers,
    ).status_code == 404

    created = client.post(
        "/subscriptions",
        json={"email": "watcher@example.test", "tags": ["dingo"]},
        headers=headers,
    )
    assert created.status_code == 201
    subscription_id = created.json()["subscription_id"]
    assert client.get("/subscriptions", headers=headers).json()["results"][0]["email"] == "watcher@example.test"

    updated = client.put(
        f"/subscriptions/{subscription_id}",
        json={"email": "new@example.test", "tags": ["night"], "expected_version": 1},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.delete(f"/subscriptions/{subscription_id}", headers=headers).status_code == 204
    assert client.get("/subscriptions", headers=headers).json() == {"results": []}


def test_owner_scoped_media_delete_by_id_returns_single_result_contract() -> None:
    client, _ = client_with_feature_routes()
    headers = {"Authorization": "Bearer valid-token"}

    response = client.delete(f"/media/{MEDIA_ID}", headers=headers)

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "deleted"
    assert result["media_id"] == str(MEDIA_ID)
    assert "error" in result
