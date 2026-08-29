from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import unquote
from uuid import UUID

from backend.common.contracts.models import MediaPreparedEvent, MediaRecord, TaggingCompletedEvent
from backend.common.providers.fakes import (
    DeterministicInferenceService,
    FixedClock,
    InMemoryMediaRepository,
    InMemoryObjectStorage,
    RecordingEventPublisher,
    SequenceIdGenerator,
)
from backend.common.providers.interfaces import InferenceResult
from backend.tagging.worker.service import TaggingWorker


NOW = datetime(2026, 8, 22, 11, 0, tzinfo=UTC)
OCCURRED_AT = datetime(2026, 8, 22, 10, 55, tzinfo=UTC)
MEDIA_ID = UUID("22222222-2222-4222-8222-222222222222")
INPUT_EVENT_ID = UUID("33333333-3333-4333-8333-333333333333")
OUTPUT_EVENT_ID = UUID("44444444-4444-4444-8444-444444444444")


class CountingRepository(InMemoryMediaRepository):
    def __init__(self) -> None:
        super().__init__()
        self.upsert_count = 0

    def upsert(self, record) -> None:
        self.upsert_count += 1
        super().upsert(record)


class CountingInference(DeterministicInferenceService):
    def __init__(self, results) -> None:
        super().__init__(results)
        self.calls: list[list[str]] = []

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        self.calls.append(list(storage_uris))
        return super().infer(storage_uris)


class RecordingStorage(InMemoryObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    def get_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        return super().get_bytes(key)


def image_event() -> MediaPreparedEvent:
    return MediaPreparedEvent(
        schema_version="1.0",
        event_id=INPUT_EVENT_ID,
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        sha256="a" * 64,
        media_type="image",
        original_storage_uri="s3://media/originals/a/camera%20one.jpg",
        thumbnail_storage_uri="s3://media/derived/a/thumbnail.jpg",
        frame_storage_uris=[],
        occurred_at=OCCURRED_AT,
    )


def make_worker(*, event: MediaPreparedEvent, result: InferenceResult):
    storage = RecordingStorage()
    inference_uris = (
        [str(event.original_storage_uri)]
        if event.media_type == "image"
        else [str(uri) for uri in event.frame_storage_uris]
    )
    for uri in inference_uris:
        key = uri.split("/", 3)[-1]
        storage.put_bytes(key, b"prepared-image", content_type="image/jpeg")
    inference = CountingInference({tuple(inference_uris): result})
    repository = CountingRepository()
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
    repository.upsert_count = 0
    publisher = RecordingEventPublisher()
    worker = TaggingWorker(
        storage=storage,
        inference=inference,
        repository=repository,
        publisher=publisher,
        clock=FixedClock(NOW),
        ids=SequenceIdGenerator([OUTPUT_EVENT_ID]),
    )
    return worker, storage, inference, repository, publisher


def test_image_event_upserts_complete_ready_record_then_publishes_event() -> None:
    event = image_event()
    worker, storage, inference, repository, publisher = make_worker(
        event=event,
        result=InferenceResult(tag_counts={"dingo": 2}, model_version="1.2.3"),
    )

    outcome = worker.process(event.model_dump(mode="json"))

    assert outcome.duplicate is False
    assert outcome.record == repository.get("owner-123", MEDIA_ID)
    assert outcome.record.file_name == "camera one.jpg"
    assert outcome.record.status == "ready"
    assert outcome.record.tag_counts == {"dingo": 2}
    assert outcome.record.manual_tags == []
    assert outcome.record.model_version == "1.2.3"
    assert outcome.record.created_at == OCCURRED_AT
    assert outcome.record.updated_at == NOW
    assert storage.reads == ["originals/a/camera%20one.jpg"]
    assert inference.calls == [[str(event.original_storage_uri)]]
    assert publisher.events == [outcome.completed_event]
    assert outcome.completed_event == TaggingCompletedEvent(
        schema_version="1.0",
        event_id=OUTPUT_EVENT_ID,
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        tag_counts={"dingo": 2},
        model_version="1.2.3",
        occurred_at=NOW,
    )


def test_duplicate_delivery_does_not_repeat_inference_upsert_or_event() -> None:
    event = image_event()
    worker, _, inference, repository, publisher = make_worker(
        event=event,
        result=InferenceResult(tag_counts={"dingo": 1}, model_version="1.2.3"),
    )

    first = worker.process(event)
    second = worker.process(event)

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.record == first.record
    assert second.completed_event is None
    assert inference.calls == [[str(event.original_storage_uri)]]
    assert repository.upsert_count == 1
    assert len(publisher.events) == 1


def test_video_uses_only_ordered_prepared_frame_uris() -> None:
    frames = [
        "s3://media/derived/v/frame-000001.jpg",
        "s3://media/derived/v/frame-000002.jpg",
    ]
    event = MediaPreparedEvent(
        schema_version="1.0",
        event_id=INPUT_EVENT_ID,
        media_id=MEDIA_ID,
        owner_sub="owner-123",
        sha256="b" * 64,
        media_type="video",
        original_storage_uri="s3://media/originals/v/clip.mp4",
        thumbnail_storage_uri="s3://media/derived/v/thumbnail.jpg",
        frame_storage_uris=frames,
        occurred_at=OCCURRED_AT,
    )
    worker, storage, inference, _, _ = make_worker(
        event=event,
        result=InferenceResult(
            tag_counts={"dingo": 1, "wombat": 2},
            model_version="2.0.0",
        ),
    )

    outcome = worker.process(event)

    assert inference.calls == [frames]
    assert storage.reads == [
        "derived/v/frame-000001.jpg",
        "derived/v/frame-000002.jpg",
    ]
    assert outcome.record.original_storage_uri == event.original_storage_uri
    assert outcome.record.tag_counts == {"dingo": 1, "wombat": 2}
