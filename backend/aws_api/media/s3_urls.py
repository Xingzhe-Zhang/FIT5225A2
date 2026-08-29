from __future__ import annotations

from urllib.parse import unquote, urlparse

from backend.azure_api.management.service import InvalidSignedUrl


class S3SignedUrlNormalizer:
    """Converts only this bucket's HTTPS presigned URLs to canonical S3 URIs."""

    def __init__(self, *, bucket_name: str, region: str) -> None:
        self._bucket = bucket_name
        self._hosts = {
            f"{bucket_name}.s3.{region}.amazonaws.com".casefold(),
            f"{bucket_name}.s3.amazonaws.com".casefold(),
        }

    def canonical_storage_uri(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").casefold() not in self._hosts:
            raise InvalidSignedUrl("URL was not issued for the configured S3 bucket")
        try:
            port = parsed.port
        except ValueError as error:
            raise InvalidSignedUrl("S3 URL authority is invalid") from error
        if parsed.username or parsed.password or port not in {None, 443}:
            raise InvalidSignedUrl("S3 URL authority is invalid")
        key = unquote(parsed.path).lstrip("/")
        if not key or any(segment in {"", ".", ".."} for segment in key.split("/")):
            raise InvalidSignedUrl("URL does not contain a safe object key")
        return f"s3://{self._bucket}/{key}"
