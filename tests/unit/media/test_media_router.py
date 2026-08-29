from __future__ import annotations

import importlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.common.auth.models import AuthContext
from backend.common.contracts.models import MediaRecord
from backend.common.errors.models import ApiError
from backend.common.providers.fakes import DeterministicObjectUrlSigner, InMemoryMediaRepository


class StaticVerifier:
    def verify(self, token: str) -> AuthContext:
        if token != "valid-token":
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401)
        return AuthContext(sub="owner-123")


def record(
    media_id: str,
    owner_sub: str,
    media_type: str = "image",
    status: str = "ready",
) -> MediaRecord:
    return MediaRecord(
        media_id=UUID(media_id),
        owner_sub=owner_sub,
        sha256="a" * 64,
        file_name="camera.jpg" if media_type == "image" else "clip.mp4",
        media_type=media_type,
        original_storage_uri=(
            f"s3://pba-media/originals/{media_id}/{'a' * 64}/camera.jpg"
            if media_type == "image"
            else f"s3://pba-media/originals/{media_id}/{'a' * 64}/clip.mp4"
        ),
        thumbnail_storage_uri=(
            f"s3://pba-media/derived/{media_id}/{'a' * 64}/thumbnail.jpg"
            if status in {"prepared", "ready"}
            else None
        ),
        tag_counts={"dingo": 2},
        manual_tags=[],
        model_version="speciesnet-1.0",
        status=status,
        created_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
    )


def test_media_route_requires_auth_and_returns_only_signed_owner_media() -> None:
    media = importlib.import_module("backend.aws_api.media")
    router_module = importlib.import_module("backend.aws_api.media.router")
    repository = InMemoryMediaRepository()
    owned_image = record("11111111-1111-4111-8111-111111111111", "owner-123")
    owned_video = record("22222222-2222-4222-8222-222222222222", "owner-123", "video")
    foreign = record("33333333-3333-4333-8333-333333333333", "owner-999")
    processing = record("44444444-4444-4444-8444-444444444444", "owner-123", status="processing")
    for item in (owned_image, owned_video, foreign, processing):
        repository.upsert(item)
    service = media.MediaLibraryService(
        repository=repository,
        url_signer=DeterministicObjectUrlSigner(
            upload_base_url="https://uploads.example.test",
            download_base_url="https://downloads.example.test",
        ),
    )
    app = FastAPI()
    app.state.auth_verifier = StaticVerifier()

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    app.include_router(router_module.create_media_router(service))
    client = TestClient(app)

    assert client.get("/media").status_code == 401
    response = client.get("/media", headers={"Authorization": "Bearer valid-token"})

    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {
                "media_id": str(owned_image.media_id),
                "file_name": "camera.jpg",
                "media_type": "image",
                "status": "ready",
                "original_url": f"https://downloads.example.test/originals/{owned_image.media_id}/{'a' * 64}/camera.jpg",
                "thumbnail_url": f"https://downloads.example.test/derived/{owned_image.media_id}/{'a' * 64}/thumbnail.jpg",
                "tag_counts": {"dingo": 2},
                "manual_tags": [],
                "failure_code": None,
                "failure_message": None,
            },
            {
                "media_id": str(owned_video.media_id),
                "file_name": "clip.mp4",
                "media_type": "video",
                "status": "ready",
                "original_url": f"https://downloads.example.test/originals/{owned_video.media_id}/{'a' * 64}/clip.mp4",
                "thumbnail_url": f"https://downloads.example.test/derived/{owned_video.media_id}/{'a' * 64}/thumbnail.jpg",
                "tag_counts": {"dingo": 2},
                "manual_tags": [],
                "failure_code": None,
                "failure_message": None,
            },
            {
                "media_id": str(processing.media_id),
                "file_name": "camera.jpg",
                "media_type": "image",
                "status": "processing",
                "original_url": None,
                "thumbnail_url": None,
                "tag_counts": {"dingo": 2},
                "manual_tags": [],
                "failure_code": None,
                "failure_message": None,
            },
        ]
    }
