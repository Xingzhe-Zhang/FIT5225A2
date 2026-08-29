from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from backend.common.contracts.models import MediaPreparedEvent
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


def test_ready_record_and_completion_event_match_shared_json_schemas() -> None:
    root = Path(__file__).parents[3]
    storage = InMemoryObjectStorage()
    storage.put_bytes("originals/a/camera.jpg", b"image", content_type="image/jpeg")
    original_uri = "s3://media/originals/a/camera.jpg"
    worker = TaggingWorker(
        storage=storage,
        inference=DeterministicInferenceService(
            {
                (original_uri,): InferenceResult(
                    tag_counts={"dingo": 2},
                    model_version="1.0.0",
                )
            }
        ),
        repository=InMemoryMediaRepository(),
        publisher=RecordingEventPublisher(),
        clock=FixedClock(datetime(2026, 8, 22, 12, 0, tzinfo=UTC)),
        ids=SequenceIdGenerator(
            [UUID("44444444-4444-4444-8444-444444444444")]
        ),
    )
    event = MediaPreparedEvent(
        schema_version="1.0",
        event_id=UUID("33333333-3333-4333-8333-333333333333"),
        media_id=UUID("22222222-2222-4222-8222-222222222222"),
        owner_sub="owner-123",
        sha256="a" * 64,
        media_type="image",
        original_storage_uri=original_uri,
        thumbnail_storage_uri="s3://media/derived/a/thumbnail.jpg",
        frame_storage_uris=[],
        occurred_at=datetime(2026, 8, 22, 11, 55, tzinfo=UTC),
    )

    outcome = worker.process(event)

    schemas = root / "contracts" / "schemas"
    record_schema = json.loads((schemas / "media-record.schema.json").read_text())
    event_schema = json.loads(
        (schemas / "tagging-completed-event.schema.json").read_text()
    )
    Draft202012Validator(record_schema, format_checker=FormatChecker()).validate(
        outcome.record.model_dump(mode="json")
    )
    assert outcome.completed_event is not None
    Draft202012Validator(event_schema, format_checker=FormatChecker()).validate(
        outcome.completed_event.model_dump(mode="json")
    )
