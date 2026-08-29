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
        self.claimed = False
        self.failure: tuple[str, str] | None = None
        self.released_tokens: list[str] = []

    def find_by_original_uri(self, storage_uri: str):
        return self.reservation if self.reservation.original_storage_uri == storage_uri else None

    def claim_event(self, media_id: UUID, event_token: str) -> bool:
        del event_token
        if self.claimed or media_id != self.reservation.media_id:
            return False
        self.claimed = True
        self.reservation = replace(self.reservation, status="processing")
        return True

    def mark_prepared(self, media_id: UUID, thumbnail_uri: str) -> None:
        raise AssertionError(f"unexpected prepared state for {media_id}: {thumbnail_uri}")

    def release_event(self, media_id: UUID, event_token: str) -> None:
        assert media_id == self.reservation.media_id
        self.released_tokens.append(event_token)
        self.claimed = False
        self.reservation = replace(self.reservation, status="uploaded")

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


def event(key: str) -> dict[str, object]:
    return {
        "Records": [
            {
                "eventID": "event-1",
                "s3": {
                    "bucket": {"name": "pba-media"},
                    "object": {"key": quote_plus(key), "versionId": "v1"},
                },
            }
        ]
    }


def build_handler(
    source: bytes,
    *,
    metadata_checksum: str | None = None,
    content_type: str = "image/png",
    media_type: str = "image",
    content_length: int | None = None,
):
    handler_module = importlib.import_module("backend.media_processor.images.handler")
    thumbnail_module = importlib.import_module("backend.media_processor.images.thumbnail")
    checksum = hashlib.sha256(source).hexdigest()
    key = f"originals/{checksum}/camera.png"
    reservation = handler_module.ImageReservation(
        media_id=MEDIA_ID,
        owner_sub="owner",
        sha256=checksum,
        media_type=media_type,
        original_storage_uri=f"s3://pba-media/{key}",
        status="reserved",
    )
    reservations = ReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(key, source, content_type=content_type)
    publisher = RecordingEventPublisher()
    handler = handler_module.ImageEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=Inspector(
            handler_module.ObjectHead(
                content_type=content_type,
                metadata={"sha256": metadata_checksum or checksum},
                version_id="v1",
                content_length=content_length,
            )
        ),
        reservations=reservations,
        publisher=publisher,
        thumbnailer=thumbnail_module.PillowThumbnailer(thumbnail_module.ThumbnailConfig()),
        clock=FixedClock(datetime(2026, 8, 22, 11, 0, tzinfo=UTC)),
        ids=SequenceIdGenerator([]),
        recompute_checksum=True,
    )
    return handler, key, checksum, storage, reservations, publisher


def test_checksum_metadata_mismatch_is_quarantined_without_event() -> None:
    source = b"source-that-must-not-be-decoded"
    handler, key, checksum, storage, reservations, publisher = build_handler(
        source,
        metadata_checksum="b" * 64,
    )

    result = handler.handle(event(key))

    assert result == ["failed"]
    assert reservations.failure == (
        "IMAGE_CHECKSUM_MISMATCH",
        "Uploaded image checksum does not match its reservation",
    )
    assert not storage.exists(key)
    assert storage.exists(f"quarantine/{checksum}/camera.png")
    assert publisher.events == []
    assert handler.handle(event(key)) == ["duplicate"]
    assert reservations.released_tokens == []


@pytest.mark.parametrize(
    ("content_type", "media_type"),
    [
        ("application/octet-stream", "image"),
        ("image/png", "video"),
    ],
)
def test_unsupported_media_is_failed_without_processing(content_type: str, media_type: str) -> None:
    handler, key, _, _, reservations, publisher = build_handler(
        b"invalid-media",
        content_type=content_type,
        media_type=media_type,
    )

    assert handler.handle(event(key)) == ["failed"]
    assert reservations.failure is not None
    assert reservations.failure[0] == "IMAGE_MEDIA_TYPE_INVALID"
    assert publisher.events == []


def test_corrupt_supported_image_has_stable_failed_status() -> None:
    handler, key, _, _, reservations, publisher = build_handler(b"corrupt-png")

    assert handler.handle(event(key)) == ["failed"]
    assert reservations.failure == ("IMAGE_CORRUPT", "Image could not be decoded")
    assert publisher.events == []
    assert handler.handle(event(key)) == ["duplicate"]
    assert reservations.released_tokens == []


@pytest.mark.parametrize(
    ("content_length", "expected_code"),
    [
        (25 * 1024 * 1024 + 1, "IMAGE_SIZE_INVALID"),
        (999, "IMAGE_CONTENT_LENGTH_MISMATCH"),
    ],
)
def test_s3_content_length_is_checked_before_image_processing(
    content_length: int,
    expected_code: str,
) -> None:
    handler, key, _, storage, reservations, _ = build_handler(
        b"small-payload",
        content_length=content_length,
    )

    assert handler.handle(event(key)) == ["failed"]
    assert reservations.failure is not None
    assert reservations.failure[0] == expected_code
    assert storage.exists(key)
