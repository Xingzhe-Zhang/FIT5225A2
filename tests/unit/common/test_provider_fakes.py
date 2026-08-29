from __future__ import annotations

import importlib
from datetime import UTC, datetime
from uuid import UUID


def fakes_module():
    return importlib.import_module("backend.common.providers.fakes")


def make_record(*, media_id: str, owner_sub: str = "owner"):
    contracts = importlib.import_module("backend.common.contracts.models")
    now = datetime(2026, 8, 22, 4, 0, tzinfo=UTC)
    return contracts.MediaRecord(
        media_id=UUID(media_id),
        owner_sub=owner_sub,
        sha256="a" * 64,
        file_name="camera.jpg",
        media_type="image",
        original_storage_uri=f"s3://media/originals/{media_id}/camera.jpg",
        thumbnail_storage_uri=f"s3://media/derived/{media_id}/thumbnail.jpg",
        tag_counts={"dingo": 2, "wombat": 1},
        manual_tags=["night"],
        model_version="1.0.0",
        status="ready",
        created_at=now,
        updated_at=now,
    )


def test_in_memory_object_storage_round_trip_and_prefix_listing() -> None:
    fakes = fakes_module()
    storage = fakes.InMemoryObjectStorage()

    storage.put_bytes("originals/a/file.jpg", b"image", content_type="image/jpeg")
    storage.put_bytes("derived/a/thumb.jpg", b"thumb", content_type="image/jpeg")

    assert storage.get_bytes("originals/a/file.jpg") == b"image"
    assert storage.list_keys("derived/") == ["derived/a/thumb.jpg"]
    storage.delete_keys(["originals/a/file.jpg"])
    assert storage.exists("originals/a/file.jpg") is False


def test_repository_reservation_is_idempotent_for_owner_and_checksum() -> None:
    fakes = fakes_module()
    repository = fakes.InMemoryMediaRepository()
    first_id = UUID("11111111-1111-4111-8111-111111111111")
    second_id = UUID("22222222-2222-4222-8222-222222222222")

    first = repository.reserve_upload("owner", "a" * 64, first_id)
    second = repository.reserve_upload("owner", "a" * 64, second_id)

    assert first.created is True
    assert first.media_id == first_id
    assert second.created is False
    assert second.media_id == first_id


def test_repository_reclaims_reservation_after_materialized_media_is_deleted() -> None:
    fakes = fakes_module()
    repository = fakes.InMemoryMediaRepository()
    first_id = UUID("11111111-1111-4111-8111-111111111111")
    second_id = UUID("22222222-2222-4222-8222-222222222222")
    checksum = "b" * 64

    first = repository.reserve_upload("owner", checksum, first_id)
    assert first.created is True
    repository.upsert(
        make_record(media_id=str(first_id), owner_sub="owner").model_copy(
            update={"sha256": checksum}
        )
    )
    assert repository.delete("owner", first_id) is True

    replacement = repository.reserve_upload("owner", checksum, second_id)
    assert replacement.created is True
    assert replacement.media_id == second_id


def test_fixed_clock_and_sequence_ids_are_deterministic() -> None:
    fakes = fakes_module()
    now = datetime(2026, 8, 22, 4, 0, tzinfo=UTC)
    clock = fakes.FixedClock(now)
    ids = fakes.SequenceIdGenerator(
        [UUID("11111111-1111-4111-8111-111111111111")]
    )

    assert clock.now_utc() == now
    assert ids.new_uuid() == UUID("11111111-1111-4111-8111-111111111111")


def test_recording_notifier_records_complete_message() -> None:
    fakes = fakes_module()
    notifier = fakes.RecordingNotifier()
    notifier.send("verified@example.com", "Dingo detected", "Open the application")
    assert notifier.messages == [
        fakes.NotificationMessage(
            recipient="verified@example.com",
            subject="Dingo detected",
            body="Open the application",
        )
    ]


def test_repository_queries_and_deletes_owner_scoped_records() -> None:
    fakes = fakes_module()
    repository = fakes.InMemoryMediaRepository()
    owned = make_record(media_id="11111111-1111-4111-8111-111111111111")
    foreign = make_record(
        media_id="22222222-2222-4222-8222-222222222222",
        owner_sub="other-owner",
    )
    repository.upsert(owned)
    repository.upsert(foreign)

    assert repository.query_by_tags("owner", {"dingo": 2}) == [owned]
    assert repository.query_by_tags("owner", {"dingo": 3}) == []
    assert repository.query_by_tags("owner", {"night": 1}) == [owned]
    assert repository.query_by_tags("owner", {"night": 2}) == []
    assert repository.query_by_species("owner", "night") == [owned]
    species = make_record(media_id="33333333-3333-4333-8333-333333333333").model_copy(
        update={"tag_counts": {"Alectura_lathami": 1}}
    )
    repository.upsert(species)
    assert repository.query_by_species("owner", "Alectura lathami") == [species]
    assert repository.find_by_storage_uri("owner", str(owned.thumbnail_storage_uri)) == owned

    assert repository.delete("owner", owned.media_id) is True
    assert repository.delete("owner", owned.media_id) is False
    assert repository.get("other-owner", foreign.media_id) == foreign


def test_recording_event_publisher_preserves_event_order() -> None:
    fakes = fakes_module()
    publisher = fakes.RecordingEventPublisher()

    publisher.publish({"event_id": "first"})
    publisher.publish({"event_id": "second"})

    assert publisher.events == [
        {"event_id": "first"},
        {"event_id": "second"},
    ]


def test_deterministic_inference_and_url_signer_use_configured_results() -> None:
    fakes = fakes_module()
    inference = fakes.DeterministicInferenceService(
        {
            ("s3://media/derived/a/thumbnail.jpg",): fakes.InferenceResult(
                tag_counts={"dingo": 2},
                model_version="1.0.0",
            )
        }
    )
    signer = fakes.DeterministicObjectUrlSigner(
        upload_base_url="https://uploads.example.test",
        download_base_url="https://downloads.example.test",
    )

    assert inference.infer(["s3://media/derived/a/thumbnail.jpg"]) == fakes.InferenceResult(
        tag_counts={"dingo": 2},
        model_version="1.0.0",
    )
    assert signer.create_upload_url(
        "originals/a/camera.jpg",
        content_type="image/jpeg",
        checksum_sha256="a" * 64,
        expires_in_seconds=900,
    ) == "https://uploads.example.test/originals/a/camera.jpg"
    assert signer.create_download_url(
        "derived/a/thumbnail.jpg",
        expires_in_seconds=900,
    ) == "https://downloads.example.test/derived/a/thumbnail.jpg"
