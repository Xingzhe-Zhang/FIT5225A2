from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import unquote
from uuid import UUID

import pytest

from backend.common.contracts.models import MediaPreparedEvent, MediaRecord
from backend.common.providers.fakes import (
    DeterministicInferenceService,
    FixedClock,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
    RecordingEventPublisher,
    SequenceIdGenerator,
)
from backend.common.providers.interfaces import InferenceResult
from backend.tagging.worker.errors import (
    PermanentTaggingError,
    RetryClassification,
    TransientTaggingError,
)
from backend.tagging.worker.service import TaggingWorker


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
MEDIA_ID = UUID("22222222-2222-4222-8222-222222222222")
COMPLETED_ID = UUID("44444444-4444-4444-8444-444444444444")


def prepared_event(*, media_type: str = "image", frames: list[str] | None = None):
    return MediaPreparedEvent(
        schema_version="1.0",
        event_id=EVENT_ID,
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        sha256="a" * 64,
        media_type=media_type,
        original_storage_uri="s3://media/originals/a/camera.jpg",
        thumbnail_storage_uri="s3://media/derived/a/thumbnail.jpg",
        frame_storage_uris=[] if frames is None else frames,
        occurred_at=datetime(2026, 8, 22, 11, 55, tzinfo=UTC),
    )


def make_worker(*, storage, inference, repository, publisher):
    return TaggingWorker(
        storage=storage,
        inference=inference,
        repository=repository,
        publisher=publisher,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([COMPLETED_ID]),
    )


def seed_prepared(repository, event: MediaPreparedEvent) -> None:
    repository.upsert(MediaRecord(
        media_id=event.media_id,
        owner_sub=event.owner_sub,
        sha256=event.sha256,
        file_name=unquote(str(event.original_storage_uri).rsplit("/", 1)[-1]),
        media_type=event.media_type,
        original_storage_uri=event.original_storage_uri,
        thumbnail_storage_uri=event.thumbnail_storage_uri,
        tag_counts={},
        manual_tags=[],
        model_version="pending",
        status="prepared",
        created_at=event.occurred_at,
        updated_at=event.occurred_at,
    ))


def test_schema_invalid_delivery_is_permanent_without_partial_record() -> None:
    repository = InMemoryMediaRepository()
    publisher = RecordingEventPublisher()
    worker = make_worker(
        storage=InMemoryObjectStorage(),
        inference=DeterministicInferenceService({}),
        repository=repository,
        publisher=publisher,
    )

    with pytest.raises(PermanentTaggingError) as captured:
        worker.process({"schema_version": "1.0"})

    assert captured.value.retry_classification is RetryClassification.PERMANENT
    assert captured.value.record is None
    assert publisher.events == []


def test_video_without_prepared_frames_records_permanent_failure() -> None:
    event = prepared_event(media_type="video", frames=[])
    repository = InMemoryMediaRepository()
    seed_prepared(repository, event)
    publisher = RecordingEventPublisher()
    worker = make_worker(
        storage=InMemoryObjectStorage(),
        inference=DeterministicInferenceService({}),
        repository=repository,
        publisher=publisher,
    )

    with pytest.raises(PermanentTaggingError, match="at least one prepared frame") as captured:
        worker.process(event)

    assert captured.value.retry_classification is RetryClassification.PERMANENT
    failed = repository.get("owner-123", MEDIA_ID)
    assert failed == captured.value.record
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_code == "TAGGING_INPUT_INVALID"
    assert failed.failure_message == "Tagging failed: video requires at least one prepared frame"
    assert failed.tag_counts == {}
    assert failed.model_version == "unavailable"
    assert failed.updated_at == NOW
    assert publisher.events == []


def test_missing_prepared_object_is_transient_and_retry_can_complete() -> None:
    event = prepared_event()
    original_uri = str(event.original_storage_uri)
    storage = InMemoryObjectStorage()
    repository = InMemoryMediaRepository()
    seed_prepared(repository, event)
    publisher = RecordingEventPublisher()
    worker = make_worker(
        storage=storage,
        inference=DeterministicInferenceService(
            {
                (original_uri,): InferenceResult(
                    tag_counts={"dingo": 1},
                    model_version="1.0.0",
                )
            }
        ),
        repository=repository,
        publisher=publisher,
    )

    with pytest.raises(TransientTaggingError) as captured:
        worker.process(event)

    assert captured.value.retry_classification is RetryClassification.TRANSIENT
    assert captured.value.record is None
    assert repository.get("owner-123", MEDIA_ID).status == "prepared"
    assert publisher.events == []

    storage.put_bytes("originals/a/camera.jpg", b"image", content_type="image/jpeg")
    outcome = worker.process(event)

    assert outcome.record.status == "ready"
    assert outcome.record.tag_counts == {"dingo": 1}
    assert len(publisher.events) == 1


def test_transient_publish_retry_reuses_ready_record_and_same_completion_event() -> None:
    class CountingRepository(InMemoryMediaRepository):
        def __init__(self) -> None:
            super().__init__()
            self.upserts = 0

        def upsert(self, record) -> None:
            self.upserts += 1
            super().upsert(record)

    class CountingInference(DeterministicInferenceService):
        def __init__(self, results) -> None:
            super().__init__(results)
            self.calls = 0

        def infer(self, storage_uris: list[str]) -> InferenceResult:
            self.calls += 1
            return super().infer(storage_uris)

    class FlakyPublisher(RecordingEventPublisher):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def publish(self, event: object) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("event bus timeout")
            super().publish(event)

    event = prepared_event()
    uri = str(event.original_storage_uri)
    storage = InMemoryObjectStorage()
    storage.put_bytes("originals/a/camera.jpg", b"image", content_type="image/jpeg")
    repository = CountingRepository()
    seed_prepared(repository, event)
    repository.upserts = 0
    inference = CountingInference(
        {(uri,): InferenceResult(tag_counts={"dingo": 1}, model_version="1.0.0")}
    )
    publisher = FlakyPublisher()
    worker = make_worker(
        storage=storage,
        inference=inference,
        repository=repository,
        publisher=publisher,
    )

    with pytest.raises(TransientTaggingError, match="event bus timeout") as captured:
        worker.process(event)

    assert captured.value.record is not None
    assert captured.value.record.status == "ready"
    retry = worker.process(event)
    duplicate = worker.process(event)

    assert retry.completed_event is not None
    assert retry.completed_event.event_id == COMPLETED_ID
    assert retry.duplicate is False
    assert duplicate.duplicate is True
    assert inference.calls == 1
    assert repository.upserts == 1
    assert publisher.attempts == 2
    assert publisher.events == [retry.completed_event]


def test_stale_event_after_media_deletion_does_not_recreate_failed_record() -> None:
    event = prepared_event()
    storage = InMemoryObjectStorage()
    storage.put_bytes("originals/a/camera.jpg", b"image", content_type="image/jpeg")
    repository = InMemoryMediaRepository()
    publisher = RecordingEventPublisher()
    worker = make_worker(
        storage=storage,
        inference=DeterministicInferenceService({}),
        repository=repository,
        publisher=publisher,
    )

    with pytest.raises(PermanentTaggingError, match="stale prepared event") as captured:
        worker.process(event)

    assert captured.value.record is None
    assert repository.get("owner-123", MEDIA_ID) is None
    assert publisher.events == []
