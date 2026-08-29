from __future__ import annotations

import tempfile
from pathlib import Path

from backend.common.providers.interfaces import Clock, InferenceService, MediaRepository, ObjectStorage
from backend.media_processor.images.thumbnail import PillowThumbnailer
from backend.media_processor.videos.processing import VideoProcessor


class LocalMediaProcessingService:
    """Deterministic local/test bridge to the real image and video processors."""

    def __init__(
        self,
        *,
        bucket_name: str,
        repository: MediaRepository,
        storage: ObjectStorage,
        thumbnailer: PillowThumbnailer,
        video_processor: VideoProcessor,
        clock: Clock,
        inference: InferenceService | None = None,
    ) -> None:
        self._bucket_name = bucket_name
        self._repository = repository
        self._storage = storage
        self._thumbnailer = thumbnailer
        self._video_processor = video_processor
        self._clock = clock
        self._inference = inference

    def process_uploaded_object(self, key: str, content_type: str, sha256: str) -> None:
        storage_uri = f"s3://{self._bucket_name}/{key}"
        record = self._repository.find_by_original_uri(storage_uri)
        if record is None:
            raise ValueError("uploaded object does not have a reservation")
        parts = key.split("/")
        if (
            len(parts) != 4
            or parts[0] != "originals"
            or parts[1] != str(record.media_id)
            or parts[2] != record.sha256
            or sha256 != record.sha256
        ):
            raise ValueError("uploaded object key does not match its reservation")

        now = self._clock.now_utc()
        self._repository.upsert(
            record.model_copy(update={"status": "uploaded", "expires_at": None, "updated_at": now})
        )
        processing = record.model_copy(
            update={"status": "processing", "expires_at": None, "updated_at": now}
        )
        self._repository.upsert(processing)
        derived_prefix = f"derived/{record.media_id}/{record.sha256}"

        try:
            source = self._storage.get_bytes(key)
            if record.media_type == "image":
                if content_type not in {"image/jpeg", "image/png"}:
                    raise ValueError("uploaded image content type is invalid")
                thumbnail = self._thumbnailer.create(source).data
                inference_uris = [storage_uri]
            else:
                if content_type not in {"video/mp4", "video/quicktime"}:
                    raise ValueError("uploaded video content type is invalid")
                with tempfile.TemporaryDirectory(prefix="pba-local-video-") as temporary:
                    source_path = Path(temporary) / "source.video"
                    source_path.write_bytes(source)
                    video = self._video_processor.process(source_path, size_bytes=len(source))
                frame_uris: list[str] = []
                for timestamp, frame in zip(video.timestamps, video.frames, strict=True):
                    frame_key = f"{derived_prefix}/frames/{timestamp:06d}.jpg"
                    self._storage.put_bytes(
                        frame_key,
                        frame,
                        content_type="image/jpeg",
                    )
                    frame_uris.append(f"s3://{self._bucket_name}/{frame_key}")
                thumbnail = video.representative_thumbnail
                inference_uris = frame_uris

            thumbnail_key = f"{derived_prefix}/thumbnail.jpg"
            thumbnail_uri = f"s3://{self._bucket_name}/{thumbnail_key}"
            self._storage.put_bytes(thumbnail_key, thumbnail, content_type="image/jpeg")
            update = {
                "status": "prepared",
                "thumbnail_storage_uri": thumbnail_uri,
                "updated_at": self._clock.now_utc(),
            }
            if self._inference is not None:
                inferred = self._inference.infer(inference_uris)
                update.update(
                    status="ready",
                    tag_counts=inferred.tag_counts,
                    model_version=inferred.model_version,
                )
            self._repository.upsert(processing.model_copy(update=update))
        except Exception:
            self._repository.upsert(
                processing.model_copy(
                    update={
                        "status": "failed",
                        "failure_code": "MEDIA_PROCESSING_FAILED",
                        "failure_message": "The uploaded media could not be processed",
                        "updated_at": self._clock.now_utc(),
                    }
                )
            )
            raise
