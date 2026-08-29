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


class ReservationStore:
    def __init__(self, reservation: object) -> None:
        self.reservation = reservation
        self.released_tokens: list[str] = []
        self.failure: tuple[str, str] | None = None

    def find_by_original_uri(self, storage_uri: str):
        return self.reservation if self.reservation.original_storage_uri == storage_uri else None

    def claim_event(self, media_id: UUID, event_token: str) -> bool:
        del event_token
        if media_id != self.reservation.media_id or self.reservation.status in {"failed", "prepared"}:
            return False
        self.reservation = replace(self.reservation, status="processing")
        return True

    def release_claim(self, media_id: UUID, event_token: str) -> None:
        assert media_id == self.reservation.media_id
        self.released_tokens.append(event_token)
        self.reservation = replace(self.reservation, status="uploaded")

    def mark_prepared(self, media_id: UUID, thumbnail_uri: str, frame_uris: list[str]) -> None:
        raise AssertionError(f"unexpected prepared state for {media_id}: {thumbnail_uri}, {frame_uris}")

    def mark_failed(self, media_id: UUID, code: str, message: str) -> None:
        assert media_id == self.reservation.media_id
        self.failure = (code, message)
        self.reservation = replace(self.reservation, status="failed")


class Inspector:
    def __init__(self, head: object) -> None:
        self.head = head

    def inspect(self, key: str):
        del key
        return self.head


class RaisingProcessor:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def process(self, source, *, size_bytes: int | None = None):
        assert source.read_bytes() == b"video-source"
        assert size_bytes == len(b"video-source")
        raise self.error


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


def build_handler(error: BaseException):
    handler_module = importlib.import_module("backend.media_processor.videos.handler")
    source = b"video-source"
    checksum = hashlib.sha256(source).hexdigest()
    key = f"originals/{checksum}/clip.mp4"
    reservation = handler_module.VideoReservation(
        media_id=MEDIA_ID,
        owner_sub="owner",
        sha256=checksum,
        media_type="video",
        original_storage_uri=f"s3://pba-media/{key}",
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(key, source, content_type="video/mp4")
    handler = handler_module.VideoEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=Inspector(handler_module.ObjectHead("video/mp4", {"sha256": checksum}, "version-1")),
        reservations=reservations,
        publisher=RecordingEventPublisher(),
        processor=RaisingProcessor(error),
        clock=FixedClock(datetime(2026, 8, 22, 12, 0, tzinfo=UTC)),
        ids=SequenceIdGenerator([]),
        recompute_checksum=True,
    )
    return handler, reservations, key


@pytest.mark.parametrize(
    ("code", "retry"),
    [
        ("VIDEO_CORRUPT", False),
        ("VIDEO_FRAME_EXTRACTION_FAILED", False),
        ("VIDEO_PROCESSING_TIMEOUT", True),
        ("VIDEO_BACKEND_UNAVAILABLE", True),
    ],
)
def test_video_processing_error_is_failed_or_retried_by_code(code: str, retry: bool) -> None:
    processing = importlib.import_module("backend.media_processor.videos.processing")
    handler, reservations, key = build_handler(processing.VideoProcessingError(code, "processor failure"))

    if retry:
        with pytest.raises(processing.VideoProcessingError, match="processor failure"):
            handler.handle(event(key))
        assert reservations.released_tokens == ["video-event-1:version-1"]
        assert reservations.failure is None
    else:
        assert handler.handle(event(key)) == ["failed"]
        assert reservations.released_tokens == []
        assert reservations.failure == (code, "processor failure")


def test_unknown_programming_error_is_not_swallowed() -> None:
    handler, reservations, key = build_handler(RuntimeError("unexpected bug"))

    with pytest.raises(RuntimeError, match="unexpected bug"):
        handler.handle(event(key))

    assert reservations.released_tokens == ["video-event-1:version-1"]
    assert reservations.failure is None
