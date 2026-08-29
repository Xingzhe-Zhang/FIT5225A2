from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping
from urllib.parse import unquote, urlparse
from uuid import UUID

from pydantic import ValidationError

from backend.common.contracts.models import (
    MediaPreparedEvent,
    MediaRecord,
    TaggingCompletedEvent,
)
from backend.common.providers.interfaces import (
    Clock,
    EventPublisher,
    IdGenerator,
    InferenceService,
    MediaRepository,
    ObjectStorage,
)
from .errors import PermanentTaggingError, TaggingWorkerError, TransientTaggingError


@dataclass(frozen=True, slots=True)
class TaggingOutcome:
    record: MediaRecord
    completed_event: TaggingCompletedEvent | None
    duplicate: bool


class TaggingWorker:
    """Validate and tag one prepared-media delivery using baseline providers."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        inference: InferenceService,
        repository: MediaRepository,
        publisher: EventPublisher,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._storage = storage
        self._inference = inference
        self._repository = repository
        self._publisher = publisher
        self._clock = clock
        self._ids = ids
        self._pending_events: dict[UUID, TaggingCompletedEvent] = {}

    def process(self, raw_event: MediaPreparedEvent | Mapping[str, object]) -> TaggingOutcome:
        try:
            event = (
                raw_event
                if isinstance(raw_event, MediaPreparedEvent)
                else MediaPreparedEvent.model_validate(raw_event)
            )
        except ValidationError as exc:
            raise PermanentTaggingError(
                f"invalid MediaPreparedEvent: {exc}",
                record=None,
            ) from exc
        existing = self._repository.get(event.owner_sub, event.media_id)
        if existing is not None and existing.status == "ready":
            pending = self._pending_events.get(event.event_id)
            if pending is not None:
                self._publish(event.event_id, pending, existing)
                return TaggingOutcome(
                    record=existing,
                    completed_event=pending,
                    duplicate=False,
                )
            return TaggingOutcome(record=existing, completed_event=None, duplicate=True)

        try:
            self._validate_shape(event)
            inference_uris = self._inference_uris(event)
            for uri in inference_uris:
                self._storage.get_bytes(_storage_key(uri))
            result = self._inference.infer(inference_uris)
            now = self._clock.now_utc()
            record = MediaRecord(
                media_id=event.media_id,
                owner_sub=event.owner_sub,
                sha256=event.sha256,
                file_name=_file_name(str(event.original_storage_uri)),
                media_type=event.media_type,
                original_storage_uri=event.original_storage_uri,
                thumbnail_storage_uri=event.thumbnail_storage_uri,
                tag_counts=result.tag_counts,
                manual_tags=[],
                model_version=result.model_version,
                status="ready",
                created_at=event.occurred_at,
                updated_at=now,
            )
        except Exception as exc:
            self._record_failure(event, exc)

        try:
            self._repository.upsert(record)
        except Exception as exc:
            self._raise_classified("ready record upsert failed", exc, record=None)
        completed_event = TaggingCompletedEvent(
            schema_version="1.0",
            event_id=self._ids.new_uuid(),
            media_id=event.media_id,
            owner_sub=event.owner_sub,
            tag_counts=result.tag_counts,
            model_version=result.model_version,
            occurred_at=now,
        )
        self._pending_events[event.event_id] = completed_event
        self._publish(event.event_id, completed_event, record)
        return TaggingOutcome(
            record=record,
            completed_event=completed_event,
            duplicate=False,
        )

    def _record_failure(self, event: MediaPreparedEvent, cause: Exception) -> None:
        if self._classified_error_type(cause) is TransientTaggingError:
            self._raise_classified("tagging failed", cause, record=None)
        now = self._clock.now_utc()
        detail = str(cause) or cause.__class__.__name__
        failure_code = "TAGGING_INPUT_INVALID" if isinstance(cause, ValueError) else "TAGGING_INFERENCE_FAILED"
        failed_record = MediaRecord(
            media_id=event.media_id,
            owner_sub=event.owner_sub,
            sha256=event.sha256,
            file_name=_file_name(str(event.original_storage_uri)),
            media_type=event.media_type,
            original_storage_uri=event.original_storage_uri,
            thumbnail_storage_uri=event.thumbnail_storage_uri,
            tag_counts={},
            manual_tags=[],
            model_version="unavailable",
            status="failed",
            failure_code=failure_code,
            failure_message=f"Tagging failed: {detail}"[:500],
            created_at=event.occurred_at,
            updated_at=now,
        )
        try:
            self._repository.upsert(failed_record)
        except Exception as repository_error:
            raise TransientTaggingError(
                f"failed status could not be persisted: {repository_error}",
                record=None,
            ) from repository_error
        self._raise_classified("tagging failed", cause, record=failed_record)

    def _publish(
        self,
        source_event_id: UUID,
        event: TaggingCompletedEvent,
        record: MediaRecord,
    ) -> None:
        try:
            self._publisher.publish(event)
        except Exception as exc:
            self._raise_classified("completion event publish failed", exc, record=record)
        self._pending_events.pop(source_event_id, None)

    @staticmethod
    def _raise_classified(
        context: str,
        cause: Exception,
        *,
        record: MediaRecord | None,
    ) -> None:
        error_type = TaggingWorker._classified_error_type(cause)
        detail = str(cause) or cause.__class__.__name__
        raise error_type(f"{context}: {detail}", record=record) from cause

    @staticmethod
    def _classified_error_type(cause: Exception) -> type[TaggingWorkerError]:
        if isinstance(cause, (TimeoutError, ConnectionError, OSError, KeyError)):
            return TransientTaggingError
        return PermanentTaggingError

    @staticmethod
    def _validate_shape(event: MediaPreparedEvent) -> None:
        if event.media_type == "video" and not event.frame_storage_uris:
            raise ValueError("video requires at least one prepared frame")
        if event.media_type == "image" and event.frame_storage_uris:
            raise ValueError("image events cannot contain prepared video frames")
        if event.media_type == "image" and event.thumbnail_storage_uri is None:
            raise ValueError("image events require a thumbnail storage URI")

    @staticmethod
    def _inference_uris(event: MediaPreparedEvent) -> list[str]:
        if event.media_type == "video":
            return [str(uri) for uri in event.frame_storage_uris]
        return [str(event.original_storage_uri)]


def _storage_key(uri: str) -> str:
    return urlparse(uri).path.lstrip("/")


def _file_name(uri: str) -> str:
    return unquote(PurePosixPath(urlparse(uri).path).name)
