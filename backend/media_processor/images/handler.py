from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import unquote_plus
from uuid import UUID

from backend.common.contracts.models import MediaPreparedEvent
from backend.common.providers.interfaces import (
    Clock,
    EventPublisher,
    IdGenerator,
    ObjectStorage,
)

from .thumbnail import ImageProcessingError, PillowThumbnailer


ImageStatus = Literal["reserved", "uploaded", "processing", "prepared", "failed"]


@dataclass(frozen=True, slots=True)
class ImageReservation:
    media_id: UUID
    owner_sub: str
    sha256: str
    media_type: str
    original_storage_uri: str
    status: ImageStatus


@dataclass(frozen=True, slots=True)
class ObjectHead:
    content_type: str
    metadata: dict[str, str]
    version_id: str | None = None
    content_length: int | None = None


class ImageReservationStore(Protocol):
    def find_by_original_uri(self, storage_uri: str) -> ImageReservation | None: ...
    def claim_event(self, media_id: UUID, event_token: str) -> bool: ...
    def release_event(self, media_id: UUID, event_token: str) -> None: ...
    def mark_prepared(self, media_id: UUID, thumbnail_uri: str) -> None: ...
    def mark_failed(self, media_id: UUID, code: str, message: str) -> None: ...


class ObjectInspector(Protocol):
    def inspect(self, key: str) -> ObjectHead: ...


class PreparedEventValidator(Protocol):
    def validate(self, event: dict[str, object]) -> None: ...


class ImageEventHandler:
    def __init__(
        self,
        *,
        bucket_name: str,
        storage: ObjectStorage,
        inspector: ObjectInspector,
        reservations: ImageReservationStore,
        publisher: EventPublisher,
        thumbnailer: PillowThumbnailer,
        clock: Clock,
        ids: IdGenerator,
        recompute_checksum: bool,
        event_validator: PreparedEventValidator | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._storage = storage
        self._inspector = inspector
        self._reservations = reservations
        self._publisher = publisher
        self._thumbnailer = thumbnailer
        self._clock = clock
        self._ids = ids
        self._recompute_checksum = recompute_checksum
        self._event_validator = event_validator

    def handle(self, event: dict[str, object]) -> list[str]:
        records = event.get("Records", [])
        if not isinstance(records, list):
            return ["ignored"]
        return [self._handle_record(record) for record in records if isinstance(record, dict)]

    def _handle_record(self, record: dict[str, object]) -> str:
        bucket, key, event_token = self._event_details(record)
        if bucket != self._bucket_name or not key.startswith("originals/"):
            return "ignored"

        original_uri = f"s3://{bucket}/{key}"
        reservation = self._reservations.find_by_original_uri(original_uri)
        if reservation is None:
            return "ignored"

        head = self._inspector.inspect(key)
        if not self._reservations.claim_event(reservation.media_id, event_token):
            return "duplicate"

        try:
            if head.content_length is not None and (
                head.content_length < 1 or head.content_length > self._thumbnailer.max_input_bytes
            ):
                self._reservations.mark_failed(
                    reservation.media_id,
                    "IMAGE_SIZE_INVALID",
                    "Image byte size is outside the configured limit",
                )
                return "failed"
            source = self._storage.get_bytes(key)
            if head.content_length is not None and len(source) != head.content_length:
                self._reservations.mark_failed(
                    reservation.media_id,
                    "IMAGE_CONTENT_LENGTH_MISMATCH",
                    "Downloaded image size does not match S3 ContentLength",
                )
                return "failed"
            return self._process_claimed_event(
                reservation=reservation,
                key=key,
                head=head,
                source=source,
            )
        except Exception:
            self._reservations.release_event(reservation.media_id, event_token)
            raise

    def _process_claimed_event(
        self,
        *,
        reservation: ImageReservation,
        key: str,
        head: ObjectHead,
        source: bytes,
    ) -> str:
        if reservation.media_type != "image" or head.content_type not in {"image/jpeg", "image/png"}:
            return self._fail_and_quarantine(
                reservation,
                key,
                source,
                head.content_type,
                "IMAGE_MEDIA_TYPE_INVALID",
                "Object metadata does not describe a supported image",
            )

        derived_partition = self._derived_partition(key, reservation)
        metadata_checksum = head.metadata.get("sha256", "")
        if (
            derived_partition is None
            or metadata_checksum != reservation.sha256
        ):
            return self._fail_and_quarantine(
                reservation,
                key,
                source,
                head.content_type,
                "IMAGE_CHECKSUM_MISMATCH",
                "Uploaded image checksum does not match its reservation",
            )

        if self._recompute_checksum and hashlib.sha256(source).hexdigest() != reservation.sha256:
            return self._fail_and_quarantine(
                reservation,
                key,
                source,
                head.content_type,
                "IMAGE_CHECKSUM_MISMATCH",
                "Uploaded image checksum does not match its reservation",
            )

        try:
            thumbnail = self._thumbnailer.create(source)
        except ImageProcessingError as error:
            return self._fail_and_quarantine(
                reservation,
                key,
                source,
                head.content_type,
                error.code,
                str(error),
            )
        thumbnail_key = f"derived/{derived_partition}/thumbnail.jpg"
        thumbnail_uri = f"s3://{self._bucket_name}/{thumbnail_key}"
        self._storage.put_bytes(
            thumbnail_key,
            thumbnail.data,
            content_type="image/jpeg",
        )
        prepared = MediaPreparedEvent(
            schema_version="1.0",
            event_id=self._ids.new_uuid(),
            media_id=reservation.media_id,
            owner_sub=reservation.owner_sub,
            sha256=reservation.sha256,
            media_type="image",
            original_storage_uri=reservation.original_storage_uri,
            thumbnail_storage_uri=thumbnail_uri,
            frame_storage_uris=[],
            occurred_at=self._clock.now_utc(),
        )
        payload = prepared.model_dump(mode="json")
        if self._event_validator is not None:
            self._event_validator.validate(payload)
        self._publisher.publish(payload)
        self._reservations.mark_prepared(reservation.media_id, thumbnail_uri)
        return "processed"

    def _fail_and_quarantine(
        self,
        reservation: ImageReservation,
        source_key: str,
        source: bytes,
        content_type: str,
        code: str,
        message: str,
    ) -> str:
        file_name = source_key.rsplit("/", 1)[-1]
        partition = self._derived_partition(source_key, reservation) or reservation.sha256
        quarantine_key = f"quarantine/{partition}/{file_name}"
        self._storage.put_bytes(quarantine_key, source, content_type=content_type)
        self._storage.delete_keys([source_key])
        self._reservations.mark_failed(reservation.media_id, code, message)
        return "failed"

    @staticmethod
    def _derived_partition(key: str, reservation: ImageReservation) -> str | None:
        parts = key.split("/")
        if (
            len(parts) == 4
            and parts[0] == "originals"
            and parts[1] == str(reservation.media_id)
            and parts[2] == reservation.sha256
        ):
            return f"{parts[1]}/{parts[2]}"
        if len(parts) == 3 and parts[0] == "originals" and parts[1] == reservation.sha256:
            return reservation.sha256
        return None

    @staticmethod
    def _event_details(record: dict[str, object]) -> tuple[str, str, str]:
        s3 = record.get("s3")
        if not isinstance(s3, dict):
            return "", "", ""
        bucket_block = s3.get("bucket")
        object_block = s3.get("object")
        if not isinstance(bucket_block, dict) or not isinstance(object_block, dict):
            return "", "", ""
        bucket = str(bucket_block.get("name", ""))
        key = unquote_plus(str(object_block.get("key", "")))
        event_id = str(record.get("eventID", "unknown-event"))
        version = str(object_block.get("versionId") or object_block.get("sequencer") or "unversioned")
        return bucket, key, f"{event_id}:{version}"
