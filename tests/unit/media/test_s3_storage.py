from __future__ import annotations

import pytest

from backend.aws_api.media.s3_storage import S3Storage


class _VersionsPaginator:
    def __init__(self, client: "_VersionedClient") -> None:
        self._client = client

    def paginate(self, *, Bucket: str, Prefix: str):
        del Bucket
        self._client.version_prefixes.append(Prefix)
        return [
            {
                "Versions": [
                    item
                    for key, entries in self._client.versions.items()
                    if key.startswith(Prefix)
                    for item in entries
                ],
                "DeleteMarkers": [
                    item
                    for key, entries in self._client.delete_markers.items()
                    if key.startswith(Prefix)
                    for item in entries
                ],
            }
        ]


class _VersionedClient:
    class exceptions:
        ClientError = RuntimeError

    def __init__(self) -> None:
        self.versions: dict[str, list[dict[str, str]]] = {
            "media/a": [
                {"Key": "media/a", "VersionId": "old"},
            ],
            "media/b": [],
        }
        self.delete_markers: dict[str, list[dict[str, str]]] = {
            "media/a": [{"Key": "media/a", "VersionId": "marker"}],
            "media/b": [],
        }
        self.calls: list[list[dict[str, str]]] = []
        self.version_prefixes: list[str] = []

    def get_paginator(self, name: str):
        assert name == "list_object_versions"
        return _VersionsPaginator(self)

    def delete_objects(self, *, Bucket: str, Delete: dict[str, object]):
        del Bucket
        objects = list(Delete["Objects"])
        self.calls.append(objects)
        # Simulate the marker created by a current-object delete. The
        # subsequent versions listing must remove it too.
        for item in objects:
            if "VersionId" not in item:
                self.delete_markers.setdefault(item["Key"], []).append(
                    {"Key": item["Key"], "VersionId": "new-marker"}
                )
        return {}


def test_delete_keys_removes_current_objects_and_all_versions() -> None:
    client = _VersionedClient()

    S3Storage(client, "media-bucket").delete_keys(["media/a", "media/b", "media/a"])

    assert client.calls[0] == [{"Key": "media/a"}, {"Key": "media/b"}]
    assert {item["VersionId"] for item in client.calls[1:][0]} == {
        "old",
        "marker",
        "new-marker",
    }
    assert all(len(batch) <= 1000 for batch in client.calls)


def test_delete_keys_groups_many_sibling_keys_into_one_version_listing() -> None:
    client = _VersionedClient()
    keys = [f"frames/job/frame-{index}" for index in range(1001)]
    client.versions = {
        key: [{"Key": key, "VersionId": "old"}]
        for key in keys
    }
    client.delete_markers = {key: [] for key in keys}

    S3Storage(client, "media-bucket").delete_keys(keys)

    assert client.version_prefixes == ["frames/job/"]
    assert len(client.calls) == 5
    assert [len(batch) for batch in client.calls] == [1000, 1, 1000, 1000, 2]
    assert all(len(batch) <= 1000 for batch in client.calls)


def test_delete_keys_batches_more_than_1000_versions_and_delete_markers() -> None:
    client = _VersionedClient()
    key = "frames/job/frame-0"
    client.versions = {
        key: [
            {"Key": key, "VersionId": f"version-{index}"}
            for index in range(1001)
        ]
    }
    client.delete_markers = {
        key: [{"Key": key, "VersionId": "old-marker"}]
    }

    S3Storage(client, "media-bucket").delete_keys([key])

    assert client.version_prefixes == ["frames/job/"]
    assert len(client.calls) == 3
    assert [len(batch) for batch in client.calls] == [1, 1000, 3]
    assert all(len(batch) <= 1000 for batch in client.calls)
    assert {item["VersionId"] for item in client.calls[1] + client.calls[2]} >= {
        "old-marker",
        "new-marker",
        "version-1000",
    }


def test_delete_keys_raises_when_s3_reports_partial_errors() -> None:
    class ErrorClient(_VersionedClient):
        def delete_objects(self, **kwargs):
            del kwargs
            return {"Errors": [{"Key": "media/a", "Code": "AccessDenied"}]}

    with pytest.raises(RuntimeError, match="S3 object deletion failed"):
        S3Storage(ErrorClient(), "media-bucket").delete_keys(["media/a"])
