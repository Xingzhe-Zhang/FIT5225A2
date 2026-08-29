from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.aws_api.media.local_objects import LocalObjectUrlSigner, create_local_object_router
from backend.common.providers.fakes import InMemoryObjectStorage


SOURCE = b"camera-bytes"
SHA256 = hashlib.sha256(SOURCE).hexdigest()
KEY = f"originals/11111111-1111-4111-8111-111111111111/{SHA256}/camera.jpg"
SECRET = b"local-test-capability-secret-32-bytes"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)

    def now_utc(self) -> datetime:
        return self.value


class RecordingProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def process_uploaded_object(self, key: str, content_type: str, sha256: str) -> None:
        self.calls.append((key, content_type, sha256))


def local_stack():
    clock = MutableClock()
    storage = InMemoryObjectStorage()
    processor = RecordingProcessor()
    signer = LocalObjectUrlSigner(
        base_url="http://testserver",
        secret=SECRET,
        clock=clock,
    )
    app = FastAPI()
    app.include_router(create_local_object_router(storage, signer, processor))
    return TestClient(app), storage, processor, signer, clock


def signed_url(signer: LocalObjectUrlSigner) -> str:
    return signer.create_upload_url(
        KEY,
        content_type="image/jpeg",
        checksum_sha256=SHA256,
        expires_in_seconds=60,
    )


def headers(**changes: str) -> dict[str, str]:
    values = {"Content-Type": "image/jpeg", "x-amz-meta-sha256": SHA256}
    values.update(changes)
    return values


def replace_query(url: str, **changes: str) -> str:
    parsed = urlsplit(url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    query.update(changes)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def test_valid_capability_stores_bytes_and_invokes_processing_boundary() -> None:
    client, storage, processor, signer, _ = local_stack()

    response = client.put(signed_url(signer), content=SOURCE, headers=headers())

    assert response.status_code == 204
    assert storage.get_bytes(KEY) == SOURCE
    assert processor.calls == [(KEY, "image/jpeg", SHA256)]


def test_valid_download_capability_returns_stored_object() -> None:
    client, storage, _, signer, _ = local_stack()
    storage.put_bytes(KEY, SOURCE, content_type="image/jpeg")

    response = client.get(signer.create_download_url(KEY, expires_in_seconds=60))

    assert response.status_code == 200
    assert response.content == SOURCE
    assert response.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda url: replace_query(url, signature="0" * 64),
        lambda url: url.replace("/camera.jpg?", "/other.jpg?"),
    ],
    ids=["invalid-signature", "wrong-key"],
)
def test_invalid_download_capability_is_rejected(mutate) -> None:
    client, storage, _, signer, _ = local_stack()
    storage.put_bytes(KEY, SOURCE, content_type="image/jpeg")

    response = client.get(mutate(signer.create_download_url(KEY, expires_in_seconds=60)))

    assert response.status_code == 403


def test_expired_download_capability_is_rejected() -> None:
    client, storage, _, signer, clock = local_stack()
    storage.put_bytes(KEY, SOURCE, content_type="image/jpeg")
    url = signer.create_download_url(KEY, expires_in_seconds=60)
    clock.value += timedelta(seconds=61)

    response = client.get(url)

    assert response.status_code == 403


def test_valid_download_capability_returns_not_found_for_missing_object() -> None:
    client, _, _, signer, _ = local_stack()

    response = client.get(signer.create_download_url(KEY, expires_in_seconds=60))

    assert response.status_code == 404


@pytest.mark.parametrize(
    "mutate",
    [
        lambda url: replace_query(url, signature="0" * 64),
        lambda url: url.replace("/camera.jpg?", "/other.jpg?"),
    ],
    ids=["invalid-signature", "wrong-key"],
)
def test_invalid_signature_or_wrong_key_is_rejected(mutate) -> None:
    client, storage, processor, signer, _ = local_stack()

    response = client.put(mutate(signed_url(signer)), content=SOURCE, headers=headers())

    assert response.status_code == 403
    assert storage.exists(KEY) is False
    assert processor.calls == []


def test_expired_capability_is_rejected() -> None:
    client, storage, processor, signer, clock = local_stack()
    url = signed_url(signer)
    clock.value += timedelta(seconds=61)

    response = client.put(url, content=SOURCE, headers=headers())

    assert response.status_code == 403
    assert storage.exists(KEY) is False
    assert processor.calls == []


@pytest.mark.parametrize(
    ("payload", "request_headers"),
    [
        (SOURCE, headers(**{"Content-Type": "image/png"})),
        (SOURCE, headers(**{"x-amz-meta-sha256": "0" * 64})),
        (b"different-bytes", headers()),
    ],
    ids=["wrong-content-type", "wrong-checksum-header", "wrong-checksum-body"],
)
def test_capability_rejects_wrong_content_type_or_checksum(
    payload: bytes,
    request_headers: dict[str, str],
) -> None:
    client, storage, processor, signer, _ = local_stack()

    response = client.put(signed_url(signer), content=payload, headers=request_headers)

    assert response.status_code == 403
    assert storage.exists(KEY) is False
    assert processor.calls == []
