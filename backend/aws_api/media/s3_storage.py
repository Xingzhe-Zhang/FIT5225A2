from __future__ import annotations

from typing import Any

from backend.common.providers.interfaces import ObjectStorage


class S3Storage(ObjectStorage):
    """Boto3-backed storage adapter with permanent version-aware deletion."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

    def iter_bytes(self, key: str, *, chunk_size: int):
        body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if chunk:
                    yield chunk
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()

    def list_keys(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        return [
            item["Key"]
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix)
            for item in page.get("Contents", [])
        ]

    def delete_keys(self, keys: list[str]) -> None:
        # DeleteObjects without VersionId only creates delete markers in a
        # versioned bucket. Remove current keys first, then enumerate every
        # version and the newly-created markers for permanent removal.
        unique_keys = list(dict.fromkeys(keys))
        if not unique_keys:
            return
        for start in range(0, len(unique_keys), 1000):
            batch = unique_keys[start : start + 1000]
            # This removes an unversioned object and, in a versioned bucket,
            # creates a marker.  Listing afterwards deliberately captures
            # that marker as well as every historical version.  It also means
            # keys that never existed do not leave a marker behind.
            self._delete_objects([{"Key": key} for key in batch])
        self._delete_objects(self._versions_for_keys(unique_keys))

    def _versions_for_keys(self, keys: list[str]) -> list[dict[str, str]]:
        # Videos can have thousands of frame keys. Group by parent prefix so
        # they need a few paginated listings instead of one request per frame.
        groups: dict[str, set[str]] = {}
        for key in keys:
            parent, separator, _ = key.rpartition("/")
            prefix = f"{parent}/" if separator else key
            groups.setdefault(prefix, set()).add(key)

        try:
            paginator = self._client.get_paginator("list_object_versions")
            entries: list[dict[str, str]] = []
            for prefix, target_keys in groups.items():
                for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                    for name in ("Versions", "DeleteMarkers"):
                        for item in page.get(name, []):
                            key = item.get("Key")
                            version_id = item.get("VersionId")
                            if key in target_keys and version_id is not None:
                                entries.append({"Key": str(key), "VersionId": str(version_id)})
            return entries
        except Exception as error:
            # Some S3-compatible/unversioned implementations reject the
            # versions API entirely; regular DeleteObjects still works there.
            response = getattr(error, "response", None)
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            if code in {"InvalidRequest", "NotImplemented", "MethodNotAllowed"} or isinstance(
                error, (AttributeError, NotImplementedError)
            ):
                return []
            raise

    def _delete_objects(self, objects: list[dict[str, str]]) -> None:
        for start in range(0, len(objects), 1000):
            batch = objects[start : start + 1000]
            if not batch:
                continue
            response = self._client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": batch},
            )
            errors = response.get("Errors", []) if response else []
            if errors:
                raise RuntimeError(f"S3 object deletion failed: {errors!r}")

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise
        return True
