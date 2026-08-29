import io
import json

from backend.aws_api.queries.temporary_worker import WorkerTemporaryInferenceService


class InvokeClient:
    def invoke(self, **kwargs):
        payload = json.loads(kwargs["Payload"])
        assert payload["temporary_query"]["key"] == "temporary-query/request/image.jpg"
        return {
            "Payload": io.BytesIO(
                json.dumps({"status": "ok", "tag_counts": {"dingo": 1}, "model_version": "mdv5"}).encode()
            )
        }


def test_worker_temporary_inference_invokes_with_bucket_key() -> None:
    service = WorkerTemporaryInferenceService(
        InvokeClient(),
        "worker-function",
        bucket="media-bucket",
    )

    result = service.infer(["s3://media-bucket/temporary-query/request/image.jpg"])

    assert result.tag_counts == {"dingo": 1}
    assert result.model_version == "mdv5"
