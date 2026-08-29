"""AWS SQS/Lambda adapter for S3 media events and prepared-media tagging.

The Lambda receives one SQS message at a time. S3 notifications are processed
by the image/video handlers; a ``MediaPreparedEvent`` in the same queue is
processed by the tagging worker. Batch item failures let SQS retry only the
message that failed.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

import boto3
from dataclasses import dataclass
from urllib.parse import unquote_plus

from backend.azure_api.media.cosmos_repository import CosmosPagedMediaRepository
from backend.common.azure_cosmos_credential import load_cosmos_credential
from backend.common.contracts.models import MediaPreparedEvent, TaggingCompletedEvent
from backend.common.media_limits import (
    MAX_VIDEO_BYTES,
    MAX_VIDEO_DURATION_SECONDS,
    MAX_VIDEO_FRAMES,
    VIDEO_PROCESSING_TIMEOUT_SECONDS,
)
from backend.common.providers.fakes import FixedClock
from backend.common.providers.interfaces import EventPublisher, IdGenerator, ObjectStorage
from backend.media_processor.images.handler import ImageEventHandler, ObjectHead as ImageHead
from backend.media_processor.images.thumbnail import PillowThumbnailer, ThumbnailConfig
from backend.media_processor.videos.handler import ObjectHead as VideoHead, VideoEventHandler
from backend.media_processor.videos.processing import VideoLimits, VideoProcessor
from backend.media_processor.videos.streaming import stream_object_to_path
from backend.media_processor.videos import FfmpegVideoBackend
from backend.tagging.inference.manifest import (
    LocalArtifactReader,
    ManifestBundleLoader,
    S3ArtifactReader,
    load_configured_bundle,
)
from backend.tagging.inference.local_runtime import LocalWildlifeInferenceService
from backend.tagging.worker.errors import PermanentTaggingError
from backend.tagging.worker.service import TaggingWorker


class S3Storage(ObjectStorage):
    def __init__(self, client, bucket: str) -> None:
        self._client, self._bucket = client, bucket

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
        return [item["Key"] for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix) for item in page.get("Contents", [])]

    def delete_keys(self, keys: list[str]) -> None:
        if keys:
            self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": [{"Key": key} for key in keys]})

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except self._client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey"}:
                return False
            raise
        return True


class S3Inspector:
    def __init__(self, client, bucket: str) -> None:
        self._client, self._bucket = client, bucket

    def inspect(self, key: str):
        head = self._client.head_object(Bucket=self._bucket, Key=key)
        return ObjectHead(
            content_type=str(head.get("ContentType", "application/octet-stream")),
            metadata={str(key).lower(): str(value) for key, value in head.get("Metadata", {}).items()},
            version_id=head.get("VersionId"),
            content_length=int(head["ContentLength"]) if head.get("ContentLength") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ObjectHead:
    content_type: str
    metadata: dict[str, str]
    version_id: str | None = None
    content_length: int | None = None


class WorkerEventPublisher(EventPublisher):
    """Route prepared-media work to SQS and completion events to EventBridge."""

    def __init__(self, sqs_client, queue_url: str, events_client, event_bus_name: str) -> None:
        self._sqs = sqs_client
        self._queue_url = queue_url
        self._events = events_client
        self._event_bus_name = event_bus_name

    def publish(self, event: object) -> None:
        payload = event.model_dump(mode="json") if hasattr(event, "model_dump") else event
        if isinstance(event, TaggingCompletedEvent):
            response = self._events.put_events(
                Entries=[
                    {
                        "EventBusName": self._event_bus_name,
                        "Source": "pacific-bioarchive.tagging",
                        "DetailType": "TaggingCompleted",
                        "Detail": json.dumps(payload),
                    }
                ]
            )
            if int(response.get("FailedEntryCount", 0)):
                raise RuntimeError("EventBridge rejected the tagging completion event")
            return
        self._sqs.send_message(QueueUrl=self._queue_url, MessageBody=json.dumps(payload))


class UuidIds(IdGenerator):
    def new_uuid(self) -> UUID:
        return uuid4()


@lru_cache(maxsize=1)
def _cosmos_credential():
    secret_arn = os.environ["AZURE_WORKER_SECRET_ARN"]
    return load_cosmos_credential(
        boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION")),
        secret_arn,
    )


class ReservationAdapter:
    """Reservation protocol backed by the Cosmos media container."""

    def __init__(self, repository: CosmosPagedMediaRepository) -> None:
        self._repository = repository

    def find_by_original_uri(self, storage_uri: str):
        return self._repository.get_record_for_original(storage_uri)

    def claim_event(self, media_id: UUID, event_token: str) -> bool:
        return self._repository.claim_event(media_id, event_token)

    def release_event(self, media_id: UUID, event_token: str) -> None:
        self._repository.release_event(media_id, event_token)

    def release_claim(self, media_id: UUID, event_token: str) -> None:
        self.release_event(media_id, event_token)

    def mark_prepared(self, media_id: UUID, thumbnail_uri: str, frame_uris: list[str] | None = None) -> None:
        record = self._repository.get_by_id_any_owner(media_id)
        if record is not None:
            self._repository.upsert(
                record.model_copy(
                    update={
                        "thumbnail_storage_uri": thumbnail_uri,
                        "status": "prepared",
                        "expires_at": None,
                    }
                )
            )

    def mark_failed(self, media_id: UUID, code: str, message: str) -> None:
        record = self._repository.get_by_id_any_owner(media_id)
        if record is not None:
            self._repository.upsert(
                record.model_copy(
                    update={
                        "status": "failed",
                        "expires_at": None,
                        "failure_code": code,
                        "failure_message": message[:500],
                        "updated_at": datetime.now(UTC),
                    }
                )
            )


def _build_inference(storage: S3Storage, s3_client: object) -> LocalWildlifeInferenceService:
    manifest_uri = os.environ.get("MODEL_MANIFEST_URI", "").strip()
    if manifest_uri:
        loader = ManifestBundleLoader(
            readers={
                "file": LocalArtifactReader(),
                "s3": S3ArtifactReader(s3_client),
            },
            cache_dir=Path(os.environ.get("MODEL_CACHE_DIR", "/tmp/pba-model-cache")),
        )
        bundle = load_configured_bundle(
            loader,
            {
                "MODEL_MANIFEST_URI": manifest_uri,
                "MODEL_DEVICE": os.environ.get("MODEL_DEVICE", "cpu"),
            },
        )
        return LocalWildlifeInferenceService.from_manifest_bundle(
            storage=storage,
            bundle=bundle,
        )
    return LocalWildlifeInferenceService(
        storage=storage,
        model_dir=Path(os.environ["ML_MODEL_DIR"]),
        device=os.environ.get("ML_DEVICE", "cpu"),
        detection_threshold=float(os.environ.get("ML_DETECTION_THRESHOLD", "0.05")),
        classification_threshold=float(os.environ.get("ML_CLASSIFICATION_THRESHOLD", "0.5")),
    )


def _build():
    region = os.environ.get("AWS_REGION", "ap-southeast-2")
    bucket = os.environ["MEDIA_BUCKET"]
    queue_url = os.environ["MEDIA_QUEUE_URL"]
    s3 = boto3.client("s3", region_name=region)
    sqs = boto3.client("sqs", region_name=region)
    storage = S3Storage(s3, bucket)
    events = boto3.client("events", region_name=region)
    publisher = WorkerEventPublisher(sqs, queue_url, events, os.environ["EVENT_BUS_NAME"])
    from azure.cosmos import CosmosClient
    cosmos = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=_cosmos_credential())
    container = cosmos.get_database_client(os.environ.get("COSMOS_DATABASE", "bioarchive")).get_container_client("media")
    repository = CosmosPagedMediaRepository(container)
    reservations = ReservationAdapter(repository)
    inspector = S3Inspector(s3, bucket)
    clock = FixedClock(datetime.now(UTC))
    ids = UuidIds()
    image = ImageEventHandler(bucket_name=bucket, storage=storage, inspector=inspector, reservations=reservations, publisher=publisher, thumbnailer=PillowThumbnailer(ThumbnailConfig()), clock=clock, ids=ids, recompute_checksum=True)
    video = VideoEventHandler(bucket_name=bucket, storage=storage, inspector=inspector, reservations=reservations, publisher=publisher, processor=VideoProcessor(FfmpegVideoBackend(), VideoLimits(max_input_bytes=MAX_VIDEO_BYTES, max_duration_seconds=MAX_VIDEO_DURATION_SECONDS, max_frames=MAX_VIDEO_FRAMES, timeout_seconds=VIDEO_PROCESSING_TIMEOUT_SECONDS, supported_containers=("mp4", "mov"), supported_codecs=("h264", "hevc"))), clock=clock, ids=ids, recompute_checksum=True)
    inference = _build_inference(storage, s3)
    tagging = TaggingWorker(storage=storage, inference=inference, repository=repository, publisher=publisher, clock=clock, ids=ids)
    return s3, image, video, tagging, repository, inference


def handler(event, context):
    del context
    s3, image, video, tagging, repository, inference = _build()
    if event.get("health_check") is True:
        repository.list_for_owner("__worker_healthcheck__")
        return {"status": "ok", "database": "cosmos"}
    if event.get("model_check") is True:
        inference._get_runtime()  # type: ignore[attr-defined]
        return {"status": "ok", "model": "loaded"}
    temporary = event.get("temporary_query")
    if isinstance(temporary, dict):
        bucket = str(temporary.get("bucket", ""))
        key = str(temporary.get("key", ""))
        if bucket != os.environ["MEDIA_BUCKET"] or not key.startswith("temporary-query/"):
            raise ValueError("temporary object is outside the configured bucket")
        content_type = s3.head_object(Bucket=bucket, Key=key).get("ContentType", "")
        if content_type.startswith("video/"):
            head = s3.head_object(Bucket=bucket, Key=key)
            with tempfile.TemporaryDirectory(prefix="pba-query-video-") as temporary:
                source_path = Path(temporary) / "source.video"
                size_bytes, _ = stream_object_to_path(
                    storage,
                    key,
                    source_path,
                    max_bytes=video._processor.max_input_bytes,  # type: ignore[attr-defined]
                    expected_size=int(head["ContentLength"]),
                )
                result = video._processor.process(source_path, size_bytes=size_bytes)  # type: ignore[attr-defined]
            request_prefix = key.rsplit("/", 1)[0]
            inference_uris = []
            for timestamp, frame in zip(result.timestamps, result.frames, strict=True):
                frame_key = f"{request_prefix}/frames/{timestamp:06d}.jpg"
                storage.put_bytes(frame_key, frame, content_type="image/jpeg")
                inference_uris.append(f"s3://{bucket}/{frame_key}")
        elif content_type in {"image/jpeg", "image/png"}:
            inference_uris = [f"s3://{bucket}/{key}"]
        else:
            raise ValueError("temporary object media type is unsupported")
        inferred = inference.infer(inference_uris)
        return {"status": "ok", "tag_counts": inferred.tag_counts, "model_version": inferred.model_version}
    failures = []
    for message in event.get("Records", []):
        try:
            body = json.loads(message.get("body", "{}"))
            if "schema_version" in body and "media_id" in body and "media_type" in body:
                tagging.process(MediaPreparedEvent.model_validate(body))
            elif body.get("Records"):
                # Inspect the first object to select the media processor.
                record = body["Records"][0]
                key = unquote_plus(record["s3"]["object"]["key"])
                content_type = s3.head_object(Bucket=os.environ["MEDIA_BUCKET"], Key=key)["ContentType"]
                (video if content_type.startswith("video/") else image).handle(body)
        except PermanentTaggingError:
            # Permanent validation failures and stale events have either
            # already persisted a terminal media state or refer to a record
            # the user deleted. Acknowledge them instead of retrying to DLQ.
            continue
        except Exception:
            failures.append({"itemIdentifier": message.get("messageId", "unknown")})
    return {"batchItemFailures": failures}
