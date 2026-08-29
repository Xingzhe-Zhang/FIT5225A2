from __future__ import annotations

from typing import Callable, Protocol
from urllib.parse import unquote, urlparse

from backend.azure_api.media.repository import MediaPage
from backend.common.contracts.models import (
    MediaRecord,
    SpeciesQuery,
    TagQuery,
    ThumbnailQuery,
)


class MediaNotFoundError(LookupError):
    pass


class ThumbnailUrlError(ValueError):
    pass


class PagedMediaRepository(Protocol):
    def query_tags_page(
        self,
        owner_sub: str,
        minimum_counts: dict[str, int],
        *,
        continuation_token: str | None = None,
    ) -> MediaPage: ...

    def query_species_page(
        self,
        owner_sub: str,
        species: str,
        *,
        continuation_token: str | None = None,
    ) -> MediaPage: ...

    def find_by_storage_uri(self, owner_sub: str, storage_uri: str) -> MediaRecord | None: ...


class TrustedThumbnailNormalizer:
    def __init__(self, host_to_bucket: dict[str, str]) -> None:
        self._host_to_bucket = {
            host.casefold(): bucket for host, bucket in host_to_bucket.items()
        }

    def normalize(self, url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or host not in self._host_to_bucket:
            raise ThumbnailUrlError("thumbnail URL must use a trusted HTTPS host")
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ThumbnailUrlError("thumbnail URL authority is invalid")
        key = unquote(parsed.path).lstrip("/")
        segments = key.split("/")
        if not key.startswith("derived/") or any(segment in {"", ".", ".."} for segment in segments):
            raise ThumbnailUrlError("URL does not identify a canonical thumbnail object")
        return f"s3://{self._host_to_bucket[host]}/{key}"


class QueryService:
    def __init__(
        self,
        repository: PagedMediaRepository,
        thumbnail_normalizer: TrustedThumbnailNormalizer,
    ) -> None:
        self._repository = repository
        self._thumbnail_normalizer = thumbnail_normalizer

    def query_tags(self, owner_sub: str, payload: object) -> list[MediaRecord]:
        query = TagQuery.model_validate(payload)
        return self._collect(
            lambda token: self._repository.query_tags_page(
                owner_sub,
                query.root,
                continuation_token=token,
            )
        )

    def query_species(self, owner_sub: str, payload: object) -> list[MediaRecord]:
        query = SpeciesQuery.model_validate(payload)
        return self._collect(
            lambda token: self._repository.query_species_page(
                owner_sub,
                query.species,
                continuation_token=token,
            )
        )

    def query_thumbnail(self, owner_sub: str, payload: object) -> MediaRecord:
        query = ThumbnailQuery.model_validate(payload)
        canonical_uri = self._thumbnail_normalizer.normalize(str(query.thumbnail_url))
        record = self._repository.find_by_storage_uri(owner_sub, canonical_uri)
        if record is None:
            raise MediaNotFoundError("thumbnail was not found for the authenticated owner")
        return record

    @staticmethod
    def _collect(fetch_page: Callable[[str | None], MediaPage]) -> list[MediaRecord]:
        token: str | None = None
        seen_tokens: set[str] = set()
        records: dict[object, MediaRecord] = {}
        while True:
            page = fetch_page(token)
            for record in page.records:
                records.setdefault(record.media_id, record)
            token = page.continuation_token
            if token is None:
                break
            if token in seen_tokens:
                raise RuntimeError("repository repeated a continuation token")
            seen_tokens.add(token)
        return sorted(records.values(), key=lambda record: record.media_id)
