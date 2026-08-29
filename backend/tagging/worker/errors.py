from __future__ import annotations

from enum import Enum

from backend.common.contracts.models import MediaRecord


class RetryClassification(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class TaggingWorkerError(RuntimeError):
    retry_classification: RetryClassification

    def __init__(self, message: str, *, record: MediaRecord | None) -> None:
        super().__init__(message)
        self.record = record


class TransientTaggingError(TaggingWorkerError):
    retry_classification = RetryClassification.TRANSIENT


class PermanentTaggingError(TaggingWorkerError):
    retry_classification = RetryClassification.PERMANENT
