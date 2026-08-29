"""Idempotent automatic tagging worker."""

from .errors import (
    PermanentTaggingError,
    RetryClassification,
    TaggingWorkerError,
    TransientTaggingError,
)
from .service import TaggingOutcome, TaggingWorker

__all__ = [
    "PermanentTaggingError",
    "RetryClassification",
    "TaggingOutcome",
    "TaggingWorker",
    "TaggingWorkerError",
    "TransientTaggingError",
]
