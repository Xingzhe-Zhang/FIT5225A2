from __future__ import annotations

from typing import Protocol


class PresignClient(Protocol):
    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
    ) -> str: ...


class S3ObjectUrlSigner:
    """ObjectUrlSigner adapter without a hard dependency on boto3."""

    def __init__(self, *, client: PresignClient, bucket_name: str) -> None:
        self._client = client
        self._bucket_name = bucket_name

    def create_upload_url(
        self,
        key: str,
        *,
        content_type: str,
        checksum_sha256: str,
        expires_in_seconds: int,
    ) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket_name,
                "Key": key,
                "ContentType": content_type,
                "Metadata": {"sha256": checksum_sha256},
            },
            ExpiresIn=expires_in_seconds,
        )

    def create_download_url(self, key: str, *, expires_in_seconds: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": key},
            ExpiresIn=expires_in_seconds,
        )
