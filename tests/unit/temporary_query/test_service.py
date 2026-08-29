from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from backend.common.contracts.models import MediaRecord
from backend.common.providers.fakes import (
    DeterministicInferenceService,
    DeterministicObjectUrlSigner,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
)
from backend.common.providers.interfaces import InferenceResult
from backend.media_processor.videos.processing import VideoProcessingResult, VideoProbe
from backend.temporary_query import service as temporary_query


REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
MEDIA_ID = UUID("22222222-2222-4222-8222-222222222222")
QUERY_KEY = f"temporary-query/{REQUEST_ID}/query.jpg"
QUERY_URI = f"s3://media/{QUERY_KEY}"
JPEG = b"\xff\xd8\xff\xe0" + b"query-image"


def make_record(*, owner_sub: str = "owner") -> MediaRecord:
    now = datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    return MediaRecord(
        media_id=MEDIA_ID,
        owner_sub=owner_sub,
        sha256="a" * 64,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri="s3://media/originals/a/camera.jpg",
        thumbnail_storage_uri="s3://media/derived/a/thumbnail.jpg",
        tag_counts={"dingo": 2, "wombat": 1},
        manual_tags=[],
        model_version="speciesnet-1.0.0",
        status="ready",
        created_at=now,
        updated_at=now,
    )


def make_service(
    *,
    storage: InMemoryObjectStorage | None = None,
    repository: InMemoryMediaRepository | None = None,
    inference: object | None = None,
):
    storage = storage or InMemoryObjectStorage()
    repository = repository or InMemoryMediaRepository()
    inference = inference or DeterministicInferenceService(
        {(QUERY_URI,): InferenceResult(tag_counts={"dingo": 1, "wombat": 1}, model_version="test")}
    )
    signer = DeterministicObjectUrlSigner(
        upload_base_url="https://uploads.example.test",
        download_base_url="https://downloads.example.test",
    )
    service_type = getattr(temporary_query, "TemporaryQueryService", None)
    assert service_type is not None, "TemporaryQueryService has not been implemented"
    return service_type(
        storage=storage,
        repository=repository,
        inference=inference,
        signer=signer,
        bucket_name="media",
        max_bytes=1024,
    ), storage, repository


def test_query_uses_all_inferred_tags_and_returns_only_owned_signed_results() -> None:
    repository = InMemoryMediaRepository()
    owned = make_record()
    repository.upsert(owned)
    repository.upsert(make_record(owner_sub="other-owner"))
    service, storage, _ = make_service(repository=repository)

    response = service.query(
        owner_sub="owner",
        request_id=REQUEST_ID,
        file_name="query.jpg",
        content_type="image/jpeg",
        data=JPEG,
    )

    assert [result.media_id for result in response.results] == [MEDIA_ID]
    assert str(response.results[0].original_url) == "https://downloads.example.test/originals/a/camera.jpg"
    assert str(response.results[0].thumbnail_url) == "https://downloads.example.test/derived/a/thumbnail.jpg"
    assert storage.exists(QUERY_KEY) is False


def test_query_creates_no_media_record_or_upload_reservation() -> None:
    service, _, repository = make_service()

    assert service.query(
        owner_sub="owner",
        request_id=REQUEST_ID,
        file_name="query.jpg",
        content_type="image/jpeg",
        data=JPEG,
    ).results == []
    reservation = repository.reserve_upload("owner", "f" * 64, MEDIA_ID)
    assert reservation.created is True


@pytest.mark.parametrize(
    ("file_name", "content_type", "data"),
    [
        ("query.exe", "image/jpeg", JPEG),
        ("query.jpg", "image/png", JPEG),
        ("query.jpg", "image/jpeg", b"not-an-image"),
        ("query.jpg", "image/jpeg", b"x" * 1025),
    ],
)
def test_validation_rejects_invalid_files_without_leaving_temporary_objects(
    file_name: str,
    content_type: str,
    data: bytes,
) -> None:
    service, storage, _ = make_service()

    with pytest.raises(temporary_query.TemporaryFileValidationError):
        service.query(
            owner_sub="owner",
            request_id=REQUEST_ID,
            file_name=file_name,
            content_type=content_type,
            data=data,
        )

    assert storage.list_keys("temporary-query/") == []


def test_inference_failure_still_removes_temporary_object() -> None:
    class FailingInference:
        def infer(self, storage_uris: list[str]) -> InferenceResult:
            raise TimeoutError(f"timed out for {storage_uris[0]}")

    service, storage, _ = make_service(inference=FailingInference())

    with pytest.raises(TimeoutError):
        service.query(
            owner_sub="owner",
            request_id=REQUEST_ID,
            file_name="query.jpg",
            content_type="image/jpeg",
            data=JPEG,
        )

    assert storage.exists(QUERY_KEY) is False


def test_empty_inference_returns_no_matches_instead_of_querying_every_record() -> None:
    repository = InMemoryMediaRepository()
    repository.upsert(make_record())
    inference = DeterministicInferenceService(
        {(QUERY_URI,): InferenceResult(tag_counts={}, model_version="test")}
    )
    service, storage, _ = make_service(repository=repository, inference=inference)

    response = service.query(
        owner_sub="owner",
        request_id=REQUEST_ID,
        file_name="query.jpg",
        content_type="image/jpeg",
        data=JPEG,
    )

    assert response.results == []
    assert storage.exists(QUERY_KEY) is False


def test_partial_storage_write_is_removed_when_put_raises() -> None:
    class PartialWriteStorage(InMemoryObjectStorage):
        def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
            super().put_bytes(key, data, content_type=content_type)
            raise OSError("write acknowledgement failed")

    storage = PartialWriteStorage()
    service, _, _ = make_service(storage=storage)

    with pytest.raises(OSError):
        service.query(
            owner_sub="owner",
            request_id=REQUEST_ID,
            file_name="query.jpg",
            content_type="image/jpeg",
            data=JPEG,
        )

    assert storage.exists(QUERY_KEY) is False


@pytest.mark.parametrize(
    ("file_name", "content_type"),
    [("query.mp4", "video/mp4"), ("query.mov", "video/quicktime")],
)
def test_video_query_extracts_temporary_frames_and_cleans_all_objects(
    file_name: str,
    content_type: str,
) -> None:
    class VideoProcessor:
        def process(self, source, *, size_bytes: int | None = None) -> VideoProcessingResult:
            data = source.read_bytes()
            assert data[4:8] == b"ftyp"
            assert size_bytes == len(data)
            return VideoProcessingResult(
                probe=VideoProbe(2.0, "mp4", "h264", 640, 360),
                timestamps=(0, 1),
                frames=(JPEG, JPEG),
                representative_thumbnail=JPEG,
            )

    class RecordingInference:
        def __init__(self) -> None:
            self.uris: list[str] = []

        def infer(self, storage_uris: list[str]) -> InferenceResult:
            self.uris = storage_uris
            return InferenceResult(tag_counts={}, model_version="test")

    storage = InMemoryObjectStorage()
    inference = RecordingInference()
    repository = InMemoryMediaRepository()
    service, _, _ = make_service(storage=storage, repository=repository, inference=inference)
    service._video_processor = VideoProcessor()

    response = service.query(
        owner_sub="owner",
        request_id=REQUEST_ID,
        file_name=file_name,
        content_type=content_type,
            data=b"\x00\x00\x00\x18ftypvideo-bytes",
    )

    assert response.results == []
    assert inference.uris == [
        f"s3://media/temporary-query/{REQUEST_ID}/frames/000000.jpg",
        f"s3://media/temporary-query/{REQUEST_ID}/frames/000001.jpg",
    ]
    assert storage.list_keys("temporary-query/") == []
