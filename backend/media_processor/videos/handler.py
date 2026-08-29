from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import unquote_plus
from uuid import UUID

from backend.common.contracts.models import MediaPreparedEvent
from backend.common.media_limits import MAX_VIDEO_BYTES
from backend.common.providers.interfaces import Clock, EventPublisher, IdGenerator, ObjectStorage

from .processing import VideoProcessingError, VideoProcessor
from .streaming import stream_object_to_path


VideoStatus = Literal["reserved", "uploaded", "processing", "prepared", "failed"]


@dataclass(frozen=True, slots=True)
class VideoReservation:
    media_id: UUID
    owner_sub: str
    sha256: str
    media_type: str
    original_storage_uri: str
    status: VideoStatus


@dataclass(frozen=True, slots=True)
class ObjectHead:
    content_type: str
    metadata: dict[str, str]
    version_id: str | None = None
    content_length: int | None = None


class VideoReservationStore(Protocol):
    def find_by_original_uri(self, storage_uri: str) -> VideoReservation | None: ...
    def claim_event(self, media_id: UUID, event_token: str) -> bool: ...
    def release_claim(self, media_id: UUID, event_token: str) -> None: ...
    def mark_prepared(
        self,
        media_id: UUID,
        thumbnail_uri: str,
        frame_uris: list[str],
    ) -> None: ...
    def mark_failed(self, media_id: UUID, code: str, message: str) -> None: ...


class ObjectInspector(Protocol):
    def inspect(self, key: str) -> ObjectHead: ...


class VideoEventHandler:
    def __init__(
        self,
        *,
        bucket_name: str,
        storage: ObjectStorage,
        inspector: ObjectInspector,
        reservations: VideoReservationStore,
        publisher: EventPublisher,
        processor: VideoProcessor,
        clock: Clock,
        ids: IdGenerator,
        recompute_checksum: bool,
    ) -> None:
        self._bucket_name = bucket_name
        self._storage = storage
        self._inspector = inspector
        self._reservations = reservations
        self._publisher = publisher
        self._processor = processor
        self._clock = clock
        self._ids = ids
        self._recompute_checksum = recompute_checksum

    def handle(self, event: dict[str, object]) -> list[str]:
        records = event.get("Records", [])
        if not isinstance(records, list):
            return ["ignored"]
        return [self._handle_record(record) for record in records if isinstance(record, dict)]

    def _handle_record(self, record: dict[str, object]) -> str:
        bucket, key, event_token = self._event_details(record)
        if bucket != self._bucket_name or not key.startswith("originals/"):
            return "ignored"

        reservation = self._reservations.find_by_original_uri(f"s3://{bucket}/{key}")
        if reservation is None:
            return "ignored"
        head = self._inspector.inspect(key)
        if not self._reservations.claim_event(reservation.media_id, event_token):
            return "duplicate"

        try:
            if reservation.media_type != "video" or head.content_type not in {
                "video/mp4",
                "video/quicktime",
            }:
                self._reservations.mark_failed(
                    reservation.media_id,
                    "VIDEO_MEDIA_TYPE_INVALID",
                    "Object metadata does not describe a supported video",
                )
                return "failed"

            derived_partition = self._derived_partition(key, reservation)
            if (
                derived_partition is None
                or head.metadata.get("sha256") != reservation.sha256
            ):
                self._reservations.mark_failed(
                    reservation.media_id,
                    "VIDEO_CHECKSUM_MISMATCH",
                    "Uploaded video checksum metadata does not match its reservation",
                )
                return "failed"

            with tempfile.TemporaryDirectory(prefix="pba-video-") as temporary:
                source_path = Path(temporary) / "source.video"
                size_bytes, _ = stream_object_to_path(
                    self._storage,
                    key,
                    source_path,
                    max_bytes=getattr(self._processor, "max_input_bytes", MAX_VIDEO_BYTES),
                    expected_size=head.content_length,
                    expected_sha256=(reservation.sha256 if self._recompute_checksum else None),
                )
                result = self._processor.process(source_path, size_bytes=size_bytes)
            frame_keys = [
                f"derived/{derived_partition}/frames/{timestamp:06d}.jpg"
                for timestamp in result.timestamps
            ]
            for frame_key, frame in zip(frame_keys, result.frames, strict=True):
                self._storage.put_bytes(frame_key, frame, content_type="image/jpeg")

            thumbnail_key = f"derived/{derived_partition}/thumbnail.jpg"
            self._storage.put_bytes(
                thumbnail_key,
                result.representative_thumbnail,
                content_type="image/jpeg",
            )
            frame_uris = [f"s3://{bucket}/{frame_key}" for frame_key in frame_keys]
            thumbnail_uri = f"s3://{bucket}/{thumbnail_key}"
            prepared = MediaPreparedEvent(
                schema_version="1.0",
                event_id=self._ids.new_uuid(),
                media_id=reservation.media_id,
                owner_sub=reservation.owner_sub,
                sha256=reservation.sha256,
                media_type="video",
                original_storage_uri=reservation.original_storage_uri,
                thumbnail_storage_uri=thumbnail_uri,
                frame_storage_uris=frame_uris,
                occurred_at=self._clock.now_utc(),
            )
            self._publisher.publish(prepared.model_dump(mode="json"))
            self._reservations.mark_prepared(reservation.media_id, thumbnail_uri, frame_uris)
            return "processed"
        except VideoProcessingError as error:
            # Processing errors are expected failures from the media/backend
            # boundary.  Only errors that indicate a temporarily unavailable
            # processing dependency should be retried by the queue.  Invalid
            # media and extraction/format failures are terminal for this
            # upload, so persist the failure while retaining the claim.
            if error.code in {"VIDEO_PROCESSING_TIMEOUT", "VIDEO_BACKEND_UNAVAILABLE"}:
                self._reservations.release_claim(reservation.media_id, event_token)
                raise
            self._reservations.mark_failed(reservation.media_id, error.code, str(error))
            return "failed"
        except (TimeoutError, ConnectionError, OSError):
            self._reservations.release_claim(reservation.media_id, event_token)
            raise
        except Exception:
            # Preserve unexpected programming errors while making the event
            # eligible for a genuine queue retry instead of a duplicate.
            self._reservations.release_claim(reservation.media_id, event_token)
            raise

    @staticmethod
    def _derived_partition(key: str, reservation: VideoReservation) -> str | None:
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
