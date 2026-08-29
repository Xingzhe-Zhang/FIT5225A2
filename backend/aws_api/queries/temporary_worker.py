from __future__ import annotations

import json
from typing import Any

from backend.common.providers.interfaces import InferenceResult


class WorkerTemporaryInferenceService:
    """Invokes the ML worker synchronously for request-scoped S3 objects."""

    def __init__(self, client: Any, function_name: str, *, bucket: str) -> None:
        self._client = client
        self._function_name = function_name
        self._bucket = bucket

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        if len(storage_uris) != 1:
            raise ValueError("temporary worker inference expects one uploaded object")
        uri = storage_uris[0]
        prefix = f"s3://{self._bucket}/"
        if not uri.startswith(prefix):
            raise ValueError("temporary object is outside the configured bucket")
        response = self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps({"temporary_query": {"bucket": self._bucket, "key": uri[len(prefix):]}}).encode(),
        )
        if response.get("FunctionError"):
            raise RuntimeError("temporary ML worker failed")
        body = json.loads(response["Payload"].read())
        if body.get("status") != "ok" or not isinstance(body.get("tag_counts"), dict):
            raise RuntimeError("temporary ML worker returned an invalid response")
        return InferenceResult(
            tag_counts={str(key): int(value) for key, value in body["tag_counts"].items()},
            model_version=str(body.get("model_version", "worker")),
        )
