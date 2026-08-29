from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote
from uuid import UUID

from backend.common.contracts.models import MediaRecord
from backend.common.species_names import canonical_species_name
from backend.common.providers.interfaces import InferenceResult, ReservationResult


@dataclass(frozen=True, slots=True)
class NotificationMessage:
    recipient: str
    subject: str
    body: str


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        self._objects[key] = (bytes(data), content_type)

    def get_bytes(self, key: str) -> bytes:
        return self._objects[key][0]

    def iter_bytes(self, key: str, *, chunk_size: int):
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        data = self._objects[key][0]
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    def get_content_type(self, key: str) -> str:
        return self._objects[key][1]

    def list_keys(self, prefix: str) -> list[str]:
        return sorted(key for key in self._objects if key.startswith(prefix))

    def delete_keys(self, keys: list[str]) -> None:
        for key in keys:
            self._objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self._objects


class InMemoryMediaRepository:
    def __init__(self) -> None:
        self._reservations: dict[tuple[str, str], UUID] = {}
        self._records: dict[tuple[str, UUID], MediaRecord] = {}
        self._materialized_reservations: dict[tuple[str, str], UUID] = {}

    def reserve_upload(
        self,
        owner_sub: str,
        sha256: str,
        media_id: UUID,
        expires_at: datetime | None = None,
    ) -> ReservationResult:
        del expires_at
        key = (owner_sub, sha256)
        existing = self._reservations.get(key)
        # A media document can be lost independently of its reservation (for
        # example, after an interrupted delete).  Do not let that orphan
        # permanently block a subsequent upload.
        if (
            existing is not None
            and (owner_sub, existing) not in self._records
            and self._materialized_reservations.get(key) == existing
        ):
            self._reservations.pop(key, None)
            self._materialized_reservations.pop(key, None)
            existing = None
        if existing is not None:
            return ReservationResult(created=False, media_id=existing)
        self._materialized_reservations.pop(key, None)
        self._reservations[key] = media_id
        return ReservationResult(created=True, media_id=media_id)

    def release_upload_reservation(
        self, owner_sub: str, sha256: str, media_id: UUID | None = None
    ) -> bool:
        key = (owner_sub, sha256)
        existing = self._reservations.get(key)
        if existing is None or (media_id is not None and existing != media_id):
            return False
        del self._reservations[key]
        return True

    def upsert(self, record: MediaRecord) -> None:
        self._records[(record.owner_sub, record.media_id)] = record
        self._materialized_reservations[(record.owner_sub, record.sha256)] = record.media_id

    def get(self, owner_sub: str, media_id: UUID) -> MediaRecord | None:
        return self._records.get((owner_sub, media_id))

    def list_for_owner(self, owner_sub: str) -> list[MediaRecord]:
        return [
            record
            for (record_owner, _), record in self._records.items()
            if record_owner == owner_sub
        ]

    def find_by_original_uri(self, storage_uri: str) -> MediaRecord | None:
        return next(
            (
                record
                for record in self._records.values()
                if str(record.original_storage_uri) == storage_uri
            ),
            None,
        )

    def find_by_storage_uri(self, owner_sub: str, storage_uri: str) -> MediaRecord | None:
        for (record_owner, _), record in self._records.items():
            if record_owner != owner_sub:
                continue
            if storage_uri in {
                str(record.original_storage_uri),
                str(record.thumbnail_storage_uri) if record.thumbnail_storage_uri else None,
            }:
                return record
        return None

    def query_by_tags(
        self,
        owner_sub: str,
        minimum_counts: dict[str, int],
    ) -> list[MediaRecord]:
        normalized_required = {canonical_species_name(tag): count for tag, count in minimum_counts.items()}
        return [
            record
            for (record_owner, _), record in self._records.items()
            if record_owner == owner_sub
            and _meets_minimum_counts(record, normalized_required)
        ]

    def query_by_species(self, owner_sub: str, species: str) -> list[MediaRecord]:
        normalized = canonical_species_name(species)
        return [
            record
            for (record_owner, _), record in self._records.items()
            if record_owner == owner_sub
            and (
                any(canonical_species_name(tag) == normalized for tag in record.tag_counts)
                or any(canonical_species_name(tag) == normalized for tag in record.manual_tags)
            )
        ]

    def delete(self, owner_sub: str, media_id: UUID) -> bool:
        record = self._records.pop((owner_sub, media_id), None)
        if record is None:
            return False
        self.release_upload_reservation(owner_sub, record.sha256, media_id)
        if self._materialized_reservations.get((owner_sub, record.sha256)) == media_id:
            self._materialized_reservations.pop((owner_sub, record.sha256), None)
        return True


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


def _meets_minimum_counts(record: MediaRecord, required: dict[str, int]) -> bool:
    available = {canonical_species_name(tag): count for tag, count in record.tag_counts.items()}
    # Manual tags are presence-only, so they contribute one match.
    for tag in record.manual_tags:
        available.setdefault(canonical_species_name(tag), 1)
    return all(available.get(tag, 0) >= count for tag, count in required.items())


class DeterministicInferenceService:
    def __init__(self, results: dict[tuple[str, ...], InferenceResult]) -> None:
        self._results = dict(results)

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        return self._results[tuple(storage_uris)]


class DeterministicObjectUrlSigner:
    def __init__(self, *, upload_base_url: str, download_base_url: str) -> None:
        self._upload_base_url = upload_base_url.rstrip("/")
        self._download_base_url = download_base_url.rstrip("/")

    def create_upload_url(
        self,
        key: str,
        *,
        content_type: str,
        checksum_sha256: str,
        expires_in_seconds: int,
    ) -> str:
        del content_type, checksum_sha256, expires_in_seconds
        return f"{self._upload_base_url}/{quote(key, safe='/')}"

    def create_download_url(self, key: str, *, expires_in_seconds: int) -> str:
        del expires_in_seconds
        return f"{self._download_base_url}/{quote(key, safe='/')}"


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[NotificationMessage] = []

    def send(self, recipient: str, subject: str, body: str) -> None:
        self.messages.append(NotificationMessage(recipient, subject, body))


class InMemoryModelManifestLoader:
    def __init__(self, manifests: dict[str, dict[str, object]] | None = None) -> None:
        self._manifests = manifests or {}

    def load(self, uri: str) -> dict[str, object]:
        return dict(self._manifests[uri])


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fixed clock requires a timezone-aware value")
        self._value = value

    def now_utc(self) -> datetime:
        return self._value


class SequenceIdGenerator:
    def __init__(self, values: list[UUID]) -> None:
        self._values = deque(values)

    def new_uuid(self) -> UUID:
        if not self._values:
            raise RuntimeError("no deterministic IDs remain")
        return self._values.popleft()
