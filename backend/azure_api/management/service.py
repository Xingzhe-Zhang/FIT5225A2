"""Owner-scoped bulk manual-tag operations."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse
from uuid import UUID

from backend.common.providers.interfaces import (
    Clock,
    EventPublisher,
    IdGenerator,
    MediaRepository,
)


class InvalidSignedUrl(ValueError):
    """Raised when an assignment-facing URL is outside the result gateway."""


class SignedUrlNormalizer:
    def __init__(self, *, download_base_url: str, bucket_name: str) -> None:
        parsed = urlparse(download_base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("download_base_url must be an absolute HTTPS URL")
        self._host = parsed.netloc.casefold()
        self._base_path = parsed.path.rstrip("/")
        self._bucket_name = bucket_name

    def canonical_storage_uri(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.casefold() != self._host:
            raise InvalidSignedUrl("URL was not issued by the configured query gateway")
        path = unquote(parsed.path)
        if self._base_path:
            prefix = f"{self._base_path}/"
            if not path.startswith(prefix):
                raise InvalidSignedUrl("URL path is outside the configured gateway")
            path = path[len(self._base_path) :]
        key = path.lstrip("/")
        if not key or ".." in key.split("/"):
            raise InvalidSignedUrl("URL does not contain a safe object key")
        return f"s3://{self._bucket_name}/{key}"


@dataclass(frozen=True, slots=True)
class TagUpdateOutcome:
    url: str
    media_id: UUID | None
    status: str


class BulkTagService:
    def __init__(
        self,
        *,
        repository: MediaRepository,
        publisher: EventPublisher,
        clock: Clock,
        ids: IdGenerator,
        normalizer: SignedUrlNormalizer,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._clock = clock
        self._ids = ids
        self._normalizer = normalizer

    def update(
        self,
        *,
        owner_sub: str,
        urls: list[str],
        tags: list[str],
        operation: int,
    ) -> list[TagUpdateOutcome]:
        if operation not in (0, 1):
            raise ValueError("operation must be 0 or 1")
        normalized_tags = sorted({tag.strip().casefold() for tag in tags if tag.strip()})
        if not normalized_tags:
            raise ValueError("at least one non-empty tag is required")

        unique: dict[str, str] = {}
        invalid: list[TagUpdateOutcome] = []
        for url in urls:
            try:
                uri = self._normalizer.canonical_storage_uri(url)
            except InvalidSignedUrl:
                invalid.append(TagUpdateOutcome(url=url, media_id=None, status="invalid_url"))
                continue
            unique.setdefault(uri, url)

        outcomes = list(invalid)
        for uri, url in unique.items():
            record = self._repository.find_by_storage_uri(owner_sub, uri)
            if record is None:
                outcomes.append(TagUpdateOutcome(url=url, media_id=None, status="not_found"))
                continue
            current = self._repository.get(owner_sub, record.media_id)
            if current is None:
                outcomes.append(TagUpdateOutcome(url=url, media_id=record.media_id, status="not_found"))
                continue
            if current.updated_at != record.updated_at:
                outcomes.append(TagUpdateOutcome(url=url, media_id=record.media_id, status="conflict"))
                continue

            existing = {tag.strip().casefold() for tag in record.manual_tags}
            revised = existing | set(normalized_tags) if operation == 1 else existing - set(normalized_tags)
            if revised == existing:
                outcomes.append(TagUpdateOutcome(url=url, media_id=record.media_id, status="unchanged"))
                continue

            updated = record.model_copy(
                update={"manual_tags": sorted(revised), "updated_at": self._clock.now_utc()}
            )
            self._repository.upsert(updated)
            self._publisher.publish(
                {
                    "event_type": "manual_tags_updated",
                    "event_id": str(self._ids.new_uuid()),
                    "media_id": str(record.media_id),
                    "owner_sub": owner_sub,
                    "tags": updated.manual_tags,
                    "occurred_at": updated.updated_at.isoformat(),
                }
            )
            outcomes.append(TagUpdateOutcome(url=url, media_id=record.media_id, status="updated"))
        return outcomes
