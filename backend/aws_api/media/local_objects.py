from __future__ import annotations

import hashlib
import hmac
from typing import Protocol
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request, Response

from backend.common.providers.interfaces import Clock, ObjectStorage


LOCAL_OBJECT_PATH = "/_local/objects"


class LocalUploadProcessingBoundary(Protocol):
    def process_uploaded_object(self, key: str, content_type: str, sha256: str) -> None: ...


class LocalReadableObjectStorage(ObjectStorage, Protocol):
    def get_content_type(self, key: str) -> str: ...


class LocalObjectUrlSigner:
    """Local/test HMAC capabilities; this does not emulate a cloud signer."""

    def __init__(self, *, base_url: str, secret: bytes, clock: Clock) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP URL")
        if len(secret) < 32:
            raise ValueError("local capability secret must be at least 32 bytes")
        self._base_url = base_url.rstrip("/")
        self._secret = bytes(secret)
        self._clock = clock

    def create_upload_url(
        self,
        key: str,
        *,
        content_type: str,
        checksum_sha256: str,
        expires_in_seconds: int,
    ) -> str:
        _validated_key(key)
        _validated_expiry(expires_in_seconds)
        expires = int(self._clock.now_utc().timestamp()) + expires_in_seconds
        signature = self._signature("PUT", key, content_type, checksum_sha256, expires)
        query = urlencode(
            {
                "expires": expires,
                "content_type": content_type,
                "sha256": checksum_sha256,
                "signature": signature,
            }
        )
        return f"{self._object_url(key)}?{query}"

    def create_download_url(self, key: str, *, expires_in_seconds: int) -> str:
        _validated_key(key)
        _validated_expiry(expires_in_seconds)
        expires = int(self._clock.now_utc().timestamp()) + expires_in_seconds
        signature = self._signature("GET", key, "", "", expires)
        return f"{self._object_url(key)}?{urlencode({'expires': expires, 'signature': signature})}"

    def permits_upload(
        self,
        *,
        key: str,
        content_type: str,
        checksum_sha256: str,
        expires: int,
        signature: str,
        request_content_type: str,
        request_checksum_sha256: str,
        body_checksum_sha256: str,
    ) -> bool:
        try:
            _validated_key(key)
        except ValueError:
            return False
        if int(self._clock.now_utc().timestamp()) > expires:
            return False
        if request_content_type != content_type:
            return False
        if request_checksum_sha256 != checksum_sha256:
            return False
        if body_checksum_sha256 != checksum_sha256:
            return False
        expected = self._signature("PUT", key, content_type, checksum_sha256, expires)
        return hmac.compare_digest(signature, expected)

    def permits_download(self, *, key: str, expires: int, signature: str) -> bool:
        try:
            _validated_key(key)
        except ValueError:
            return False
        if int(self._clock.now_utc().timestamp()) > expires:
            return False
        expected = self._signature("GET", key, "", "", expires)
        return hmac.compare_digest(signature, expected)

    def _object_url(self, key: str) -> str:
        return f"{self._base_url}{LOCAL_OBJECT_PATH}/{quote(key, safe='/')}"

    def _signature(
        self,
        method: str,
        key: str,
        content_type: str,
        checksum_sha256: str,
        expires: int,
    ) -> str:
        payload = "\n".join((method, key, content_type, checksum_sha256, str(expires)))
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_local_object_router(
    storage: LocalReadableObjectStorage,
    signer: LocalObjectUrlSigner,
    processing: LocalUploadProcessingBoundary,
) -> APIRouter:
    """Expose an injected local/test store only through signed upload capabilities."""

    router = APIRouter(include_in_schema=False)

    @router.put(f"{LOCAL_OBJECT_PATH}/{{key:path}}", status_code=204)
    async def put_object(
        key: str,
        request: Request,
        expires: int,
        content_type: str,
        sha256: str,
        signature: str,
    ) -> Response:
        body = await request.body()
        if not signer.permits_upload(
            key=key,
            content_type=content_type,
            checksum_sha256=sha256,
            expires=expires,
            signature=signature,
            request_content_type=request.headers.get("content-type", ""),
            request_checksum_sha256=request.headers.get("x-amz-meta-sha256", ""),
            body_checksum_sha256=hashlib.sha256(body).hexdigest(),
        ):
            raise HTTPException(status_code=403, detail="upload capability is invalid or expired")
        storage.put_bytes(key, body, content_type=content_type)
        processing.process_uploaded_object(key, content_type, sha256)
        return Response(status_code=204)

    @router.get(f"{LOCAL_OBJECT_PATH}/{{key:path}}")
    def get_object(key: str, expires: int, signature: str) -> Response:
        if not signer.permits_download(key=key, expires=expires, signature=signature):
            raise HTTPException(status_code=403, detail="download capability is invalid or expired")
        if not storage.exists(key):
            raise HTTPException(status_code=404, detail="object was not found")
        return Response(content=storage.get_bytes(key), media_type=storage.get_content_type(key))

    return router


def _validated_expiry(expires_in_seconds: int) -> None:
    if not 1 <= expires_in_seconds <= 3600:
        raise ValueError("capability expiry must be between 1 and 3600 seconds")


def _validated_key(key: str) -> str:
    if not key or key.startswith("/") or any(part in {"", ".", ".."} for part in key.split("/")):
        raise ValueError("object key must be a relative non-traversing path")
    return key
