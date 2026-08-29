from __future__ import annotations

import hashlib
import importlib
import io
import json
from pathlib import Path
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import quote_plus
from uuid import UUID

from PIL import Image
from jsonschema import Draft202012Validator
import pytest

from backend.common.providers.fakes import (
    FixedClock,
    InMemoryObjectStorage,
    RecordingEventPublisher,
    SequenceIdGenerator,
)


MEDIA_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)


def image_bytes() -> bytes:
    image = Image.new("RGB", (640, 320), "darkgreen")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class RecordingReservationStore:
    def __init__(self, reservation: object) -> None:
        self.reservation = reservation
        self.claimed_tokens: set[str] = set()
        self.status_history = ["reserved"]
        self.failure: tuple[str, str] | None = None
        self.thumbnail_uri: str | None = None

    def find_by_original_uri(self, storage_uri: str):
        return self.reservation if self.reservation.original_storage_uri == storage_uri else None

    def claim_event(self, media_id: UUID, event_token: str) -> bool:
        if media_id != self.reservation.media_id:
            return False
        if event_token in self.claimed_tokens or self.status_history[-1] in {"prepared", "failed"}:
            return False
        self.claimed_tokens.add(event_token)
        self.status_history.extend(["uploaded", "processing"])
        self.reservation = replace(self.reservation, status="processing")
        return True

    def mark_prepared(self, media_id: UUID, thumbnail_uri: str) -> None:
        assert media_id == self.reservation.media_id
        self.thumbnail_uri = thumbnail_uri
        self.status_history.append("prepared")
        self.reservation = replace(self.reservation, status="prepared")

    def mark_failed(self, media_id: UUID, code: str, message: str) -> None:
        assert media_id == self.reservation.media_id
        self.failure = (code, message)
        self.status_history.append("failed")
        self.reservation = replace(self.reservation, status="failed")

    def release_event(self, media_id: UUID, event_token: str) -> None:
        assert media_id == self.reservation.media_id
        self.claimed_tokens.remove(event_token)
        self.status_history.append("uploaded")
        self.reservation = replace(self.reservation, status="uploaded")


class StaticInspector:
    def __init__(self, head: object) -> None:
        self.head = head
        self.calls: list[str] = []

    def inspect(self, key: str):
        self.calls.append(key)
        return self.head


class FailOncePublisher:
    def __init__(self) -> None:
        self.attempts = 0
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise TimeoutError("temporary publisher timeout")
        self.events.append(event)


def s3_event(key: str) -> dict[str, object]:
    return {
        "Records": [
            {
                "eventID": "s3-event-1",
                "s3": {
                    "bucket": {"name": "pba-media"},
                    "object": {
                        "key": quote_plus(key),
                        "versionId": "version-1",
                    },
                },
            }
        ]
    }


def test_image_event_creates_thumbnail_and_publishes_once_on_retry() -> None:
    handler_module = importlib.import_module("backend.media_processor.images.handler")
    thumbnail_module = importlib.import_module("backend.media_processor.images.thumbnail")
    source = image_bytes()
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{MEDIA_ID}/{checksum}/camera trap.png"
    original_uri = f"s3://pba-media/{original_key}"
    reservation = handler_module.ImageReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="image",
        original_storage_uri=original_uri,
        status="reserved",
    )
    repository = RecordingReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(original_key, source, content_type="image/png")
    inspector = StaticInspector(
        handler_module.ObjectHead(
            content_type="image/png",
            metadata={"sha256": checksum},
            version_id="version-1",
        )
    )
    publisher = RecordingEventPublisher()
    validator = importlib.import_module(
        "backend.media_processor.images.validation"
    ).JsonSchemaEventValidator.from_project_contract()
    handler = handler_module.ImageEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=inspector,
        reservations=repository,
        publisher=publisher,
        thumbnailer=thumbnail_module.PillowThumbnailer(
            thumbnail_module.ThumbnailConfig(max_width=160, max_height=160)
        ),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([EVENT_ID]),
        recompute_checksum=True,
        event_validator=validator,
    )

    first = handler.handle(s3_event(original_key))
    repeated = handler.handle(s3_event(original_key))

    thumbnail_key = f"derived/{MEDIA_ID}/{checksum}/thumbnail.jpg"
    assert first == ["processed"]
    assert repeated == ["duplicate"]
    assert storage.exists(thumbnail_key)
    with Image.open(io.BytesIO(storage.get_bytes(thumbnail_key))) as thumbnail:
        assert thumbnail.size == (160, 80)
    assert repository.status_history == ["reserved", "uploaded", "processing", "prepared"]
    assert repository.thumbnail_uri == f"s3://pba-media/{thumbnail_key}"
    assert len(publisher.events) == 1
    assert publisher.events[0] == {
        "schema_version": "1.0",
        "event_id": str(EVENT_ID),
        "media_id": str(MEDIA_ID),
        "owner_sub": "cognito-owner",
        "sha256": checksum,
        "media_type": "image",
        "original_storage_uri": original_uri,
        "thumbnail_storage_uri": f"s3://pba-media/{thumbnail_key}",
        "frame_storage_uris": [],
        "occurred_at": "2026-08-22T11:00:00Z",
    }
    schema_path = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "media-prepared-event.schema.json"
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        publisher.events[0]
    )


def test_derived_object_event_is_ignored_before_lookup() -> None:
    handler_module = importlib.import_module("backend.media_processor.images.handler")
    inspector = StaticInspector(None)
    handler = handler_module.ImageEventHandler(
        bucket_name="pba-media",
        storage=InMemoryObjectStorage(),
        inspector=inspector,
        reservations=RecordingReservationStore(None),
        publisher=RecordingEventPublisher(),
        thumbnailer=object(),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([]),
        recompute_checksum=True,
    )

    result = handler.handle(s3_event("derived/hash/thumbnail.jpg"))

    assert result == ["ignored"]
    assert inspector.calls == []


def test_publisher_failure_releases_claim_for_same_event_token_retry() -> None:
    handler_module = importlib.import_module("backend.media_processor.images.handler")
    thumbnail_module = importlib.import_module("backend.media_processor.images.thumbnail")
    source = image_bytes()
    checksum = hashlib.sha256(source).hexdigest()
    original_key = f"originals/{checksum}/camera.png"
    reservation = handler_module.ImageReservation(
        media_id=MEDIA_ID,
        owner_sub="cognito-owner",
        sha256=checksum,
        media_type="image",
        original_storage_uri=f"s3://pba-media/{original_key}",
        status="reserved",
    )
    repository = RecordingReservationStore(reservation)
    storage = InMemoryObjectStorage()
    storage.put_bytes(original_key, source, content_type="image/png")
    publisher = FailOncePublisher()
    handler = handler_module.ImageEventHandler(
        bucket_name="pba-media",
        storage=storage,
        inspector=StaticInspector(
            handler_module.ObjectHead(
                content_type="image/png",
                metadata={"sha256": checksum},
                version_id="version-1",
            )
        ),
        reservations=repository,
        publisher=publisher,
        thumbnailer=thumbnail_module.PillowThumbnailer(thumbnail_module.ThumbnailConfig()),
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator(
            [EVENT_ID, UUID("33333333-3333-4333-8333-333333333333")]
        ),
        recompute_checksum=True,
    )
    same_event = s3_event(original_key)

    with pytest.raises(TimeoutError, match="temporary publisher timeout"):
        handler.handle(same_event)

    assert handler.handle(same_event) == ["processed"]
    assert handler.handle(same_event) == ["duplicate"]
    assert publisher.attempts == 2
    assert len(publisher.events) == 1
    assert repository.reservation.status == "prepared"
