from __future__ import annotations

import hashlib
import importlib
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote_plus
from uuid import UUID

import pytest

from backend.common.providers.fakes import (
    FixedClock,
    InMemoryObjectStorage,
    RecordingEventPublisher,
    SequenceIdGenerator,
)


MEDIA_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


class Session:
    def __init__(self, probe: object, frames: list[bytes]) -> None:
        self._probe = probe
        self._frames = frames
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.closed = True

    def probe(self):
        return self._probe

    def extract_frames(self, timestamps: tuple[int, ...]) -> list[bytes]:
        assert timestamps == (0, 1, 2)
        return list(self._frames)


class Backend:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open(self, source, *, timeout_seconds: int) -> Session:
        assert source.read_bytes() == b"tiny-deterministic-video"
        assert timeout_seconds == 10
        return self.session


class FailingProcessor:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def process(self, source, *, size_bytes: int | None = None):
        assert source.read_bytes() == b"tiny-deterministic-video"
        assert size_bytes == len(b"tiny-deterministic-video")
        raise self.error


class ReservationStore:
    def __init__(self, reservation: object) -> None:
        self.reservation = reservation
        self.tokens: set[str] = set()
        self.status_history = ["reserved"]
        self.released_tokens: list[str] = []
        self.failures: list[tuple[str, str]] = []
        self.thumbnail_uri: str | None = None
        self.frame_uris: list[str] = []

    def find_by_original_uri(self, storage_uri: str):
        return self.reservation if self.reservation.original_storage_uri == storage_uri else None

    def claim_event(self, media_id: UUID, event_token: str) -> bool:
        if media_id != self.reservation.media_id:
            return False
        if event_token in self.tokens or self.reservation.status in {"prepared", "failed"}:
            return False
        self.tokens.add(event_token)
        self.status_history.extend(["uploaded", "processing"])
        self.reservation = replace(self.reservation, status="processing")
        return True

    def mark_prepared(
        self,
        media_id: UUID,
        thumbnail_uri: str,
        frame_uris: list[str],
    ) -> None:
        assert media_id == self.reservation.media_id
        self.thumbnail_uri = thumbnail_uri
        self.frame_uris = list(frame_uris)
        self.status_history.append("prepared")
        self.reservation = replace(self.reservation, status="prepared")

    def mark_failed(self, media_id: UUID, code: str, message: str) -> None:
        assert media_id == self.reservation.media_id
        self.failures.append((code, message))
        self.status_history.append("failed")
        self.reservation = replace(self.reservation, status="failed")

    def release_claim(self, media_id: UUID, event_token: str) -> None:
        assert media_id == self.reservation.media_id
        self.tokens.discard(event_token)
        self.released_tokens.append(event_token)
        self.reservation = replace(self.reservation, status="uploaded")


class Inspector:
    def __init__(self, head: object) -> None:
        self.head = head
        self.calls: list[str] = []

    def inspect(self, key: str):
        self.calls.append(key)
        return self.head


class FailOnceStorage(InMemoryObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def iter_bytes(self, key: str, *, chunk_size: int):
        if not self._failed:
            self._failed = True
            raise TimeoutError("temporary object-store timeout")
        yield from super().iter_bytes(key, chunk_size=chunk_size)


def event(key: str) -> dict[str, object]:
    return {
        "Records": [
            {
                "eventID": "video-event-1",
                "s3": {
                    "bucket": {"name": "pba-media"},
                    "object": {"key": quote_plus(key), "versionId": "version-1"},
                },
            }
        ]
    }


def limits(module):
    return module.VideoLimits(
        max_input_bytes=1024,
        max_duration_seconds=10.0,
        max_frames=10,
        timeout_seconds=10,
        supported_containers=("mp4",),
        supported_codecs=("h264",),
    )


def test_video_event_writes_ordered_frames_thumbnail_and_one_event() -> None:
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    processing = importlib.import_module("backend.media_processor.videos.processing")
    source = b"tiny-deterministic-video"
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{MEDIA_ID}/{checksum}/reef survey.mp4"
    original_uri = f"s3://pba-media/{original_key}"
    reservation = handler_module.VideoReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="video",
        original_storage_uri=original_uri,
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(original_key, source, content_type="video/mp4")
    inspector = Inspector(
        handler_module.ObjectHead(
            content_type="video/mp4",
            metadata={"sha256": checksum},
            version_id="version-1",
        )
    )
    publisher = RecordingEventPublisher()
    session = Session(
        processing.VideoProbe(2.4, "mp4", "h264", 640, 360),
        [b"jpeg-second-0", b"jpeg-second-1", b"jpeg-second-2"],
    )
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=inspector,
        reservations=reservations,
        publisher=publisher,
        processor=processing.VideoProcessor(Backend(session), limits(processing)),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([EVENT_ID]),
        recompute_checksum=True,
    )

    first = handler.handle(event(original_key))
    repeated = handler.handle(event(original_key))

    frame_keys = [
        f"derived/{MEDIA_ID}/{checksum}/frames/000000.jpg",
        f"derived/{MEDIA_ID}/{checksum}/frames/000001.jpg",
        f"derived/{MEDIA_ID}/{checksum}/frames/000002.jpg",
    ]
    thumbnail_key = f"derived/{MEDIA_ID}/{checksum}/thumbnail.jpg"
    assert first == ["processed"]
    assert repeated == ["duplicate"]
    assert [storage.get_bytes(key) for key in frame_keys] == [
        b"jpeg-second-0",
        b"jpeg-second-1",
        b"jpeg-second-2",
    ]
    assert storage.get_bytes(thumbnail_key) == b"jpeg-second-0"
    assert session.closed is True
    assert reservations.status_history == ["reserved", "uploaded", "processing", "prepared"]
    assert len(publisher.events) == 1
    assert publisher.events[0] == {
        "schema_version": "1.0",
        "event_id": str(EVENT_ID),
        "media_id": str(MEDIA_ID),
        "owner_sub": "cognito-owner",
        "sha256": checksum,
        "media_type": "video",
        "original_storage_uri": original_uri,
        "thumbnail_storage_uri": f"s3://pba-media/{thumbnail_key}",
        "frame_storage_uris": [f"s3://pba-media/{key}" for key in frame_keys],
        "occurred_at": "2026-08-22T12:00:00Z",
    }


def test_derived_video_object_is_ignored_before_inspection() -> None:
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    inspector = Inspector(None)
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=InMemoryObjectStorage(),
        inspector=inspector,
        reservations=ReservationStore(None),
        publisher=RecordingEventPublisher(),
        processor=object(),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([]),
        recompute_checksum=True,
    )

    assert handler.handle(event("derived/hash/frames/000000.jpg")) == ["ignored"]
    assert inspector.calls == []


def test_transient_storage_failure_releases_claim_for_event_retry() -> None:
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    processing = importlib.import_module("backend.media_processor.videos.processing")
    source = b"tiny-deterministic-video"
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{checksum}/retry.mp4"
    original_uri = f"s3://pba-media/{original_key}"
    reservation = handler_module.VideoReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="video",
        original_storage_uri=original_uri,
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = FailOnceStorage()
    storage.put_bytes(original_key, source, content_type="video/mp4")
    inspector = Inspector(
        handler_module.ObjectHead(
            content_type="video/mp4",
            metadata={"sha256": checksum},
            version_id="version-1",
        )
    )
    publisher = RecordingEventPublisher()
    session = Session(
        processing.VideoProbe(2.4, "mp4", "h264", 640, 360),
        [b"jpeg-second-0", b"jpeg-second-1", b"jpeg-second-2"],
    )
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=inspector,
        reservations=reservations,
        publisher=publisher,
        processor=processing.VideoProcessor(Backend(session), limits(processing)),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([EVENT_ID]),
        recompute_checksum=True,
    )

    with pytest.raises(TimeoutError, match="temporary object-store timeout"):
        handler.handle(event(original_key))

    assert reservations.released_tokens == ["video-event-1:version-1"]
    assert handler.handle(event(original_key)) == ["processed"]
    assert len(publisher.events) == 1


@pytest.mark.parametrize(
    ("error_code", "should_retry"),
    [
        ("VIDEO_CORRUPT", False),
        ("VIDEO_FRAME_EXTRACTION_FAILED", False),
        ("VIDEO_PROCESSING_TIMEOUT", True),
        ("VIDEO_BACKEND_UNAVAILABLE", True),
    ],
)
def test_video_processing_errors_route_permanent_and_transient_failures(
    error_code: str,
    should_retry: bool,
) -> None:
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    processing = importlib.import_module("backend.media_processor.videos.processing")
    source = b"tiny-deterministic-video"
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{checksum}/processor-error.mp4"
    reservation = handler_module.VideoReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="video",
        original_storage_uri=f"s3://pba-media/{original_key}",
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(original_key, source, content_type="video/mp4")
    inspector = Inspector(
        handler_module.ObjectHead(
            content_type="video/mp4",
            metadata={"sha256": checksum},
            version_id="version-1",
        )
    )
    error = processing.VideoProcessingError(error_code, f"processor failure: {error_code}")
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=inspector,
        reservations=reservations,
        publisher=RecordingEventPublisher(),
        processor=FailingProcessor(error),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([]),
        recompute_checksum=True,
    )

    if should_retry:
        with pytest.raises(processing.VideoProcessingError, match=f"processor failure: {error_code}"):
            handler.handle(event(original_key))
        assert reservations.released_tokens == ["video-event-1:version-1"]
    else:
        assert handler.handle(event(original_key)) == ["failed"]
        assert reservations.released_tokens == []
        assert reservations.failures == [(error_code, f"processor failure: {error_code}")]
        assert handler.handle(event(original_key)) == ["duplicate"]


@pytest.mark.parametrize(
    ("content_type", "metadata", "expected_code"),
    [
        ("image/jpeg", {"sha256": "expected"}, "VIDEO_MEDIA_TYPE_INVALID"),
        ("video/mp4", {"sha256": "wrong"}, "VIDEO_CHECKSUM_MISMATCH"),
    ],
)
def test_invalid_video_object_metadata_is_permanent_and_idempotent(
    content_type: str,
    metadata: dict[str, str],
    expected_code: str,
) -> None:
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    source = b"tiny-deterministic-video"
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{checksum}/metadata.mp4"
    reservation = handler_module.VideoReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="video",
        original_storage_uri=f"s3://pba-media/{original_key}",
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(original_key, source, content_type="video/mp4")
    if metadata.get("sha256") == "expected":
        metadata = {"sha256": checksum}
    publisher = RecordingEventPublisher()
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=Inspector(handler_module.ObjectHead(content_type, metadata, "version-1")),
        reservations=reservations,
        publisher=publisher,
        processor=object(),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([]),
        recompute_checksum=False,
    )

    assert handler.handle(event(original_key)) == ["failed"]
    assert handler.handle(event(original_key)) == ["duplicate"]
    assert reservations.failures[0][0] == expected_code
    assert publisher.events == []
