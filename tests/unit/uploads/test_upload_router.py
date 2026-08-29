from __future__ import annotations

import importlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.common.auth.models import AuthContext
from backend.common.errors.models import ApiError
from backend.common.providers.fakes import (
    DeterministicObjectUrlSigner,
    FixedClock,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
    SequenceIdGenerator,
)


class StaticVerifier:
    def verify(self, token: str) -> AuthContext:
        if token != "valid-token":
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401)
        return AuthContext(sub="owner-123")


def test_reservation_route_requires_auth_and_uses_cognito_owner() -> None:
    uploads = importlib.import_module("backend.aws_api.uploads")
    router_module = importlib.import_module("backend.aws_api.uploads.router")
    repository = InMemoryMediaRepository()
    service = uploads.UploadReservationService(
        repository=repository,
        storage=InMemoryObjectStorage(),
        url_signer=DeterministicObjectUrlSigner(
            upload_base_url="https://uploads.example.test",
            download_base_url="https://downloads.example.test",
        ),
        clock=FixedClock(datetime(2026, 8, 22, 10, 0, tzinfo=UTC)),
        ids=SequenceIdGenerator([UUID("11111111-1111-4111-8111-111111111111")]),
        bucket_name="pba-media",
        max_size_bytes=1024,
    )
    app = FastAPI()
    app.state.auth_verifier = StaticVerifier()

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    app.include_router(router_module.create_upload_router(service))
    client = TestClient(app)
    payload = {
        "file_name": "camera.jpg",
        "media_type": "image",
        "size_bytes": 512,
        "sha256": "a" * 64,
    }

    assert client.post("/uploads/reservations", json=payload).status_code == 401
    response = client.post(
        "/uploads/reservations",
        json=payload,
        headers={"Authorization": "Bearer valid-token"},
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is False
    media_id = UUID(response.json()["media_id"])
    assert repository.get("owner-123", media_id) is not None

    assert client.request(
        "DELETE",
        f"/uploads/reservations/{media_id}",
        json={"sha256": "a" * 64},
    ).status_code == 401
    cancelled = client.request(
        "DELETE",
        f"/uploads/reservations/{media_id}",
        json={"sha256": "a" * 64},
        headers={"Authorization": "Bearer valid-token"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {"media_id": str(media_id), "status": "cancelled"}
    assert repository.get("owner-123", media_id) is None
