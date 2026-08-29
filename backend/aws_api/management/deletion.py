"""Recoverable cross-cloud deletion orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import unquote
from uuid import UUID

from backend.azure_api.management.service import InvalidSignedUrl, SignedUrlNormalizer
from backend.common.providers.interfaces import (
    Clock,
    IdGenerator,
    MediaRepository,
    ObjectStorage,
)


class DeletionOperationStore(Protocol):
    def get(self, owner_sub: str, storage_uri: str) -> "DeletionOperation | None": ...
    def get_by_media_id(self, owner_sub: str, media_id: UUID) -> "DeletionOperation | None": ...
    def put(self, operation: "DeletionOperation") -> None: ...


@dataclass(slots=True)
class DeletionOperation:
    operation_id: UUID
    owner_sub: str
    storage_uri: str
    media_id: UUID
    object_keys: list[str]
    # Persist the checksum needed to release the reservation even if the
    # media document disappears before a retry.
    sha256: str = ""
    status: str = "marked"
    error: str | None = None


class InMemoryDeletionOperationStore:
    """Deterministic local recovery record; cloud adapters persist this in Cosmos DB."""

    def __init__(self) -> None:
        self._operations: dict[tuple[str, str], DeletionOperation] = {}

    def get(self, owner_sub: str, storage_uri: str) -> DeletionOperation | None:
        return self._operations.get((owner_sub, storage_uri))

    def get_by_media_id(self, owner_sub: str, media_id: UUID) -> DeletionOperation | None:
        return next(
            (
                operation
                for (operation_owner, _), operation in self._operations.items()
                if operation_owner == owner_sub and operation.media_id == media_id
            ),
            None,
        )

    def put(self, operation: DeletionOperation) -> None:
        self._operations[(operation.owner_sub, operation.storage_uri)] = operation


@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    url: str
    media_id: UUID | None
    operation_id: UUID | None
    status: str
    error: str | None = None


class CrossCloudDeleteService:
    def __init__(
        self,
        *,
        repository: MediaRepository,
        storage: ObjectStorage,
        operations: DeletionOperationStore,
        clock: Clock,
        ids: IdGenerator,
        normalizer: SignedUrlNormalizer,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._operations = operations
        self._clock = clock
        self._ids = ids
        self._normalizer = normalizer

    def delete(self, *, owner_sub: str, urls: list[str]) -> list[DeletionOutcome]:
        unique: dict[str, str] = {}
        outcomes: list[DeletionOutcome] = []
        for url in urls:
            try:
                uri = self._normalizer.canonical_storage_uri(url)
            except InvalidSignedUrl as error:
                outcomes.append(DeletionOutcome(url, None, None, "invalid_url", str(error)))
                continue
            unique.setdefault(uri, url)

        for uri, url in unique.items():
            outcomes.append(self._delete_one(owner_sub=owner_sub, storage_uri=uri, url=url))
        return outcomes

    def delete_by_id(self, *, owner_sub: str, media_id: UUID) -> DeletionOutcome:
        """Delete one owner-owned record, including reserved/failed records."""
        record = self._repository.get(owner_sub, media_id)
        if record is None:
            operation = getattr(self._operations, "get_by_media_id", lambda *_: None)(
                owner_sub, media_id
            )
            if operation is not None:
                return self._resume_without_record(operation=operation, url="")
            return DeletionOutcome("", media_id, None, "not_found")
        return self._delete_one(
            owner_sub=owner_sub,
            storage_uri=str(record.original_storage_uri),
            url="",
        )

    def _delete_one(self, *, owner_sub: str, storage_uri: str, url: str) -> DeletionOutcome:
        operation = self._operations.get(owner_sub, storage_uri)
        record = self._repository.find_by_storage_uri(owner_sub, storage_uri)
        if record is None:
            if operation is None:
                return DeletionOutcome(url, None, None, "not_found")
            return self._resume_without_record(operation=operation, url=url)

        if operation is None:
            keys = self._object_keys(record)
            operation = DeletionOperation(
                operation_id=self._ids.new_uuid(),
                owner_sub=owner_sub,
                storage_uri=storage_uri,
                media_id=record.media_id,
                object_keys=keys,
                sha256=record.sha256,
            )
            self._operations.put(operation)

        if record.status != "deleting":
            marked = record.model_copy(
                update={"status": "deleting", "updated_at": self._clock.now_utc()}
            )
            try:
                self._repository.upsert(marked)
            except Exception as error:
                return self._fail(operation, url, error)

        if operation.status != "storage_deleted":
            try:
                self._storage.delete_keys(operation.object_keys)
                operation.status = "storage_deleted"
                operation.error = None
                self._operations.put(operation)
            except Exception as error:
                return self._fail(operation, url, error)

        try:
            deleted = self._repository.delete(owner_sub, operation.media_id)
            remaining = self._repository.get(owner_sub, operation.media_id)
        except Exception as error:
            return self._fail(operation, url, error)
        if not deleted and remaining is not None:
            return self._fail(operation, url, RuntimeError("database deletion was not confirmed"))
        if remaining is not None:
            return self._fail(operation, url, RuntimeError("database record still exists"))

        try:
            self._release_reservation(operation)
        except Exception as error:
            return self._fail(operation, url, error)

        operation.status = "completed"
        operation.error = None
        self._operations.put(operation)
        return DeletionOutcome(url, operation.media_id, operation.operation_id, "deleted")

    def _resume_without_record(
        self,
        *,
        operation: DeletionOperation,
        url: str,
    ) -> DeletionOutcome:
        try:
            # A previous attempt may have persisted the operation before
            # storage deletion or reservation cleanup.  Resume those phases
            # from the durable object-key/checksum snapshot.
            if operation.status != "storage_deleted":
                self._storage.delete_keys(operation.object_keys)
                operation.status = "storage_deleted"
                operation.error = None
                self._operations.put(operation)
            remaining = self._repository.get(operation.owner_sub, operation.media_id)
            if remaining is not None:
                self._repository.delete(operation.owner_sub, operation.media_id)
                remaining = self._repository.get(operation.owner_sub, operation.media_id)
            if remaining is not None:
                raise RuntimeError("database record still exists")
            self._release_reservation(operation)
        except Exception as error:
            return self._fail(operation, url, error)
        if remaining is not None:
            return self._fail(operation, url, RuntimeError("database state is not complete"))
        operation.status = "completed"
        operation.error = None
        self._operations.put(operation)
        return DeletionOutcome(url, operation.media_id, operation.operation_id, "deleted")

    def _object_keys(self, record) -> list[str]:
        original_key = self._key(str(record.original_storage_uri))
        keys = {original_key}
        if record.thumbnail_storage_uri:
            keys.add(self._key(str(record.thumbnail_storage_uri)))
        original_parts = original_key.split("/")
        if len(original_parts) >= 4 and original_parts[0] == "originals":
            derived_prefix = f"derived/{original_parts[1]}/{original_parts[2]}/"
        else:
            derived_prefix = f"derived/{record.sha256}/"
        keys.update(self._storage.list_keys(derived_prefix))
        # Image failures quarantine the source using both the current
        # media-id/checksum partition and the legacy checksum-only partition.
        file_name = original_parts[-1]
        file_names = {file_name, unquote(file_name), record.file_name}
        keys.update(
            f"quarantine/{partition}/{name}"
            for partition in (
                f"{record.media_id}/{record.sha256}",
                record.sha256,
            )
            for name in file_names
        )
        # The media-id partition is owner-safe and may contain additional
        # historical quarantine artifacts.  The legacy checksum-only form is
        # intentionally addressed by exact file name to avoid deleting a
        # different owner's same-checksum object.
        keys.update(self._storage.list_keys(f"quarantine/{record.media_id}/{record.sha256}/"))
        return sorted(keys)

    def _release_reservation(self, operation: DeletionOperation) -> None:
        if not operation.sha256:
            return
        self._repository.release_upload_reservation(
            operation.owner_sub,
            operation.sha256,
            operation.media_id,
        )

    @staticmethod
    def _key(storage_uri: str) -> str:
        _, separator, remainder = storage_uri.partition("s3://")
        if separator != "s3://" or "/" not in remainder:
            raise ValueError("record contains an invalid storage URI")
        return remainder.split("/", 1)[1]

    def _fail(self, operation: DeletionOperation, url: str, error: Exception) -> DeletionOutcome:
        operation.error = type(error).__name__
        self._operations.put(operation)
        return DeletionOutcome(
            url,
            operation.media_id,
            operation.operation_id,
            "failed",
            type(error).__name__,
        )
