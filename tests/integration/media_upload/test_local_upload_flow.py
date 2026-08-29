from __future__ import annotations

import hashlib
import importlib
import io
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from backend.aws_api.media import MediaLibraryService
from backend.aws_api.media.local_objects import LocalObjectUrlSigner, create_local_object_router
from backend.aws_api.media.router import create_media_router
from backend.aws_api.uploads.router import create_upload_router
from backend.aws_api.uploads.service import UploadReservationService
from backend.common.auth.models import AuthContext
from backend.common.errors.models import ApiError
from backend.common.providers.fakes import (
    FixedClock,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
    SequenceIdGenerator,
)
from backend.common.providers.interfaces import InferenceResult
from backend.media_processor.images.thumbnail import PillowThumbnailer, ThumbnailConfig
from backend.media_processor.videos.processing import VideoLimits, VideoProbe, VideoProcessor


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
SECRET = b"local-e2e-capability-secret-32-bytes"
AUTH_HEADERS = {"Authorization": "Bearer valid-token"}


def jpeg_bytes(color: str = "darkgreen") -> bytes:
    image = Image.new("RGB", (96, 48), color)
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def png_bytes() -> bytes:
    image = Image.new("RGB", (640, 320), "darkgreen")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


VIDEO_THUMBNAIL = jpeg_bytes("navy")


class StaticVerifier:
    def verify(self, token: str) -> AuthContext:
        if token != "valid-token":
            raise ApiError("AUTH_TOKEN_INVALID", "Access token is invalid", 401)
        return AuthContext(sub="owner-123")


class VideoSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def probe(self) -> VideoProbe:
        return VideoProbe(1.2, "mp4", "h264", 640, 360)

    def extract_frames(self, timestamps: tuple[int, ...]) -> list[bytes]:
        assert timestamps == (0, 1)
        return [VIDEO_THUMBNAIL, jpeg_bytes("teal")]


class VideoBackend:
    def open(self, source, *, timeout_seconds: int) -> VideoSession:
        assert source.read_bytes() == b"deterministic-local-video"
        assert timeout_seconds == 10
        return VideoSession()


class RecordingInference:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        self.calls.append(storage_uris)
        return InferenceResult(tag_counts={"Bos_taurus": 2}, model_version="speciesnet-test")


def stack(media_id: UUID, *, inference: object | None = None):
    processing_module = importlib.import_module("backend.aws_api.media.local_processing")
    repository = InMemoryMediaRepository()
    storage = InMemoryObjectStorage()
    clock = FixedClock(NOW)
    signer = LocalObjectUrlSigner(base_url="http://testserver", secret=SECRET, clock=clock)
    video_processor = VideoProcessor(
        VideoBackend(),
        VideoLimits(
            max_input_bytes=1024,
            max_duration_seconds=10,
            max_frames=10,
            timeout_seconds=10,
            supported_containers=("mp4",),
            supported_codecs=("h264",),
        ),
    )
    processing = processing_module.LocalMediaProcessingService(
        bucket_name="pba-media",
        repository=repository,
        storage=storage,
        thumbnailer=PillowThumbnailer(ThumbnailConfig(max_width=160, max_height=160)),
        video_processor=video_processor,
        clock=clock,
        inference=inference,
    )
    reservations = UploadReservationService(
        repository=repository,
        storage=storage,
        url_signer=signer,
        clock=clock,
        ids=SequenceIdGenerator([media_id]),
        bucket_name="pba-media",
        max_size_bytes=1024 * 1024,
        upload_url_ttl_seconds=60,
    )
    app = FastAPI()
    app.state.auth_verifier = StaticVerifier()

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        del request
        return JSONResponse(status_code=error.status_code, content={"code": error.code})

    app.include_router(create_upload_router(reservations))
    app.include_router(create_local_object_router(storage, signer, processing))
    app.include_router(create_media_router(MediaLibraryService(repository=repository, url_signer=signer)))
    return TestClient(app), repository, storage


def upload(client: TestClient, file_name: str, media_type: str, source: bytes) -> dict[str, object]:
    sha256 = hashlib.sha256(source).hexdigest()
    reservation = client.post(
        "/uploads/reservations",
        headers=AUTH_HEADERS,
        json={
            "file_name": file_name,
            "media_type": media_type,
            "size_bytes": len(source),
            "sha256": sha256,
        },
    )
    assert reservation.status_code == 200
    payload = reservation.json()
    put = client.put(payload["upload_url"], content=source, headers=payload["upload_headers"])
    assert put.status_code == 204
    return payload


def assert_prepared_listing(client: TestClient, repository, storage, media_id: UUID) -> bytes:
    response = client.get("/media", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    result = response.json()["results"][0]
    assert result["media_id"] == str(media_id)
    assert result["status"] == "prepared"
    assert result["original_url"] is not None and "signature=" in result["original_url"]
    assert result["thumbnail_url"] is not None and "signature=" in result["thumbnail_url"]
    record = repository.get("owner-123", media_id)
    assert record is not None and record.status == "prepared"
    thumbnail_key = str(record.thumbnail_storage_uri).split("/", 3)[3]
    thumbnail = storage.get_bytes(thumbnail_key)
    with Image.open(io.BytesIO(thumbnail)) as decoded:
        assert decoded.format == "JPEG"
    return thumbnail


def test_image_upload_processes_to_real_thumbnail_and_signed_prepared_listing() -> None:
    media_id = UUID("11111111-1111-4111-8111-111111111111")
    client, repository, storage = stack(media_id)
    source = png_bytes()

    reservation = upload(client, "camera.png", "image", source)
    thumbnail = assert_prepared_listing(client, repository, storage, media_id)

    assert f"/{media_id}/" in reservation["object_key"]
    assert thumbnail != source


def test_video_upload_processes_to_representative_thumbnail_and_signed_listing() -> None:
    media_id = UUID("22222222-2222-4222-8222-222222222222")
    client, repository, storage = stack(media_id)

    reservation = upload(client, "reef.mp4", "video", b"deterministic-local-video")
    thumbnail = assert_prepared_listing(client, repository, storage, media_id)

    assert f"/{media_id}/" in reservation["object_key"]
    assert thumbnail == VIDEO_THUMBNAIL
    assert storage.list_keys(f"derived/{media_id}/")[0].endswith("frames/000000.jpg")


def test_image_upload_runs_configured_inference_and_persists_ready_tags() -> None:
    media_id = UUID("33333333-3333-4333-8333-333333333333")
    inference = RecordingInference()
    client, repository, _ = stack(media_id, inference=inference)

    reservation = upload(client, "cattle.png", "image", png_bytes())
    response = client.get("/media", headers=AUTH_HEADERS)
    result = response.json()["results"][0]

    assert result["status"] == "ready"
    assert result["tag_counts"] == {"Bos_taurus": 2}
    record = repository.get("owner-123", media_id)
    assert record is not None and record.model_version == "speciesnet-test"
    assert inference.calls == [[f"s3://pba-media/{reservation['object_key']}"]]
