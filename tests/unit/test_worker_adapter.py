from __future__ import annotations

import json
from pathlib import Path

import worker_adapter
from backend.common.contracts.models import MediaRecord, TaggingCompletedEvent
from backend.tagging.inference.manifest import LoadedModelBundle
from datetime import UTC, datetime
from uuid import UUID


class SecretClient:
    def __init__(self, payload: dict[str, str]) -> None:
        self._payload = payload

    def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
        assert SecretId == "worker-secret"
        return {"SecretString": json.dumps(self._payload)}


def test_cosmos_key_is_loaded_from_secrets_manager(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_WORKER_SECRET_ARN", "worker-secret")
    monkeypatch.setattr(worker_adapter.boto3, "client", lambda *args, **kwargs: SecretClient({"cosmos_key": "key-value"}))
    worker_adapter._cosmos_credential.cache_clear()

    assert worker_adapter._cosmos_credential() == "key-value"

    worker_adapter._cosmos_credential.cache_clear()


def test_health_check_reaches_repository_without_processing_media(monkeypatch) -> None:
    class Repository:
        def __init__(self) -> None:
            self.owners: list[str] = []

        def list_for_owner(self, owner_sub: str) -> list[object]:
            self.owners.append(owner_sub)
            return []

    repository = Repository()
    monkeypatch.setattr(
        worker_adapter,
        "_build",
        lambda: (object(), object(), object(), object(), repository, object()),
    )

    response = worker_adapter.handler({"health_check": True}, None)

    assert response == {"status": "ok", "database": "cosmos"}
    assert repository.owners == ["__worker_healthcheck__"]


def test_model_check_forces_runtime_loading(monkeypatch) -> None:
    class Inference:
        def __init__(self) -> None:
            self.loaded = False

        def _get_runtime(self) -> None:
            self.loaded = True

    inference = Inference()
    monkeypatch.setattr(
        worker_adapter,
        "_build",
        lambda: (object(), object(), object(), object(), object(), inference),
    )

    response = worker_adapter.handler({"model_check": True}, None)

    assert response == {"status": "ok", "model": "loaded"}
    assert inference.loaded is True


def test_manifest_configuration_is_used_by_production_worker(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MODEL_MANIFEST_URI", "s3://media/models/releases/2.0.0/manifest.json")
    monkeypatch.setenv("MODEL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MODEL_DEVICE", "cpu")
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"model")
    bundle = LoadedModelBundle(
        model_version="2.0.0",
        detector_path=artifact,
        classifier_path=artifact,
        labels_path=artifact,
        labels=("Dingo",),
        input_width=480,
        input_height=480,
        detection_threshold=0.1,
        classification_threshold=0.5,
        device="cpu",
    )
    captured: dict[str, object] = {}
    service = object()

    def configured_bundle(loader, environ):
        captured["loader"] = loader
        captured["environ"] = environ
        return bundle

    def from_bundle(*, storage, bundle):
        captured["storage"] = storage
        captured["bundle"] = bundle
        return service

    monkeypatch.setattr(worker_adapter, "load_configured_bundle", configured_bundle)
    monkeypatch.setattr(
        worker_adapter.LocalWildlifeInferenceService,
        "from_manifest_bundle",
        staticmethod(from_bundle),
    )
    storage = object()

    result = worker_adapter._build_inference(storage, object())

    assert result is service
    assert captured["bundle"] is bundle
    assert captured["storage"] is storage
    assert captured["environ"] == {
        "MODEL_MANIFEST_URI": "s3://media/models/releases/2.0.0/manifest.json",
        "MODEL_DEVICE": "cpu",
    }


def test_worker_event_publisher_routes_completion_to_eventbridge() -> None:
    class Sqs:
        def send_message(self, **kwargs: object) -> None:
            raise AssertionError(f"unexpected SQS call: {kwargs}")

    class Events:
        def __init__(self) -> None:
            self.entries: list[dict[str, object]] = []

        def put_events(self, *, Entries: list[dict[str, object]]) -> dict[str, object]:
            self.entries = Entries
            return {"FailedEntryCount": 0}

    events = Events()
    publisher = worker_adapter.WorkerEventPublisher(Sqs(), "queue", events, "application-events")
    publisher.publish(TaggingCompletedEvent(
        schema_version="1.0",
        event_id=UUID("11111111-1111-4111-8111-111111111111"),
        media_id=UUID("22222222-2222-4222-8222-222222222222"),
        owner_sub="owner",
        tag_counts={"dingo": 1},
        model_version="test",
        occurred_at=datetime(2026, 8, 28, tzinfo=UTC),
    ))

    assert events.entries[0]["EventBusName"] == "application-events"
    assert events.entries[0]["DetailType"] == "TaggingCompleted"
    assert json.loads(str(events.entries[0]["Detail"]))["owner_sub"] == "owner"


def test_reservation_adapter_persists_failure_diagnostics() -> None:
    media_id = UUID("22222222-2222-4222-8222-222222222222")
    now = datetime(2026, 8, 28, tzinfo=UTC)
    record = MediaRecord(
        media_id=media_id,
        owner_sub="owner",
        sha256="a" * 64,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri="s3://media/originals/camera.jpg",
        thumbnail_storage_uri=None,
        tag_counts={},
        manual_tags=[],
        model_version="pending",
        status="processing",
        created_at=now,
        updated_at=now,
    )

    class Repository:
        saved: MediaRecord | None = None

        def get_by_id_any_owner(self, requested_id: UUID) -> MediaRecord | None:
            return record if requested_id == media_id else None

        def upsert(self, value: MediaRecord) -> None:
            self.saved = value

    repository = Repository()
    worker_adapter.ReservationAdapter(repository).mark_failed(
        media_id,
        "IMAGE_CORRUPT",
        "Image could not be decoded",
    )

    assert repository.saved is not None
    assert repository.saved.status == "failed"
    assert repository.saved.failure_code == "IMAGE_CORRUPT"
    assert repository.saved.failure_message == "Image could not be decoded"
