from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from backend.common.contracts.models import MediaRecord


@dataclass(frozen=True, slots=True)
class ReservationResult:
    created: bool
    media_id: UUID


@dataclass(frozen=True, slots=True)
class InferenceResult:
    tag_counts: dict[str, int]
    model_version: str


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def iter_bytes(self, key: str, *, chunk_size: int) -> Iterable[bytes]: ...
    def list_keys(self, prefix: str) -> list[str]: ...
    def delete_keys(self, keys: list[str]) -> None: ...
    def exists(self, key: str) -> bool: ...


class MediaRepository(Protocol):
    def reserve_upload(
        self,
        owner_sub: str,
        sha256: str,
        media_id: UUID,
        expires_at: datetime | None = None,
    ) -> ReservationResult: ...
    def release_upload_reservation(
        self, owner_sub: str, sha256: str, media_id: UUID | None = None
    ) -> bool: ...
    def upsert(self, record: MediaRecord) -> None: ...
    def get(self, owner_sub: str, media_id: UUID) -> MediaRecord | None: ...
    def list_for_owner(self, owner_sub: str) -> list[MediaRecord]: ...
    def find_by_original_uri(self, storage_uri: str) -> MediaRecord | None: ...
    def find_by_storage_uri(self, owner_sub: str, storage_uri: str) -> MediaRecord | None: ...
    def query_by_tags(self, owner_sub: str, minimum_counts: dict[str, int]) -> list[MediaRecord]: ...
    def query_by_species(self, owner_sub: str, species: str) -> list[MediaRecord]: ...
    def delete(self, owner_sub: str, media_id: UUID) -> bool: ...


class ObjectUrlSigner(Protocol):
    def create_upload_url(
        self,
        key: str,
        *,
        content_type: str,
        checksum_sha256: str,
        expires_in_seconds: int,
    ) -> str: ...

    def create_download_url(self, key: str, *, expires_in_seconds: int) -> str: ...


class EventPublisher(Protocol):
    def publish(self, event: object) -> None: ...


class InferenceService(Protocol):
    def infer(self, storage_uris: list[str]) -> InferenceResult: ...


class Notifier(Protocol):
    def send(self, recipient: str, subject: str, body: str) -> None: ...


class ModelManifestLoader(Protocol):
    def load(self, uri: str) -> dict[str, object]: ...


class Clock(Protocol):
    def now_utc(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_uuid(self) -> UUID: ...
