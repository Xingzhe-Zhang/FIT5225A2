from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from backend.common.providers.interfaces import InferenceResult


class InferenceExecutionError(RuntimeError):
    """A configured model could not produce a safe structured result."""


class ImageDecodeError(InferenceExecutionError):
    """An input object could not be decoded as an image."""


@dataclass(frozen=True, slots=True)
class Detection:
    category: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class Classification:
    class_index: int
    confidence: float


@dataclass(frozen=True, slots=True)
class PredictionEvidence:
    source_uri: str
    species: str
    detection_confidence: float
    classification_confidence: float


@dataclass(frozen=True, slots=True)
class StructuredInferenceResult:
    tag_counts: dict[str, int]
    model_version: str
    evidence: tuple[PredictionEvidence, ...]


class ObjectReader(Protocol):
    def read(self, uri: str) -> bytes: ...


class DecodedImage(Protocol):
    def crop_and_resize(
        self,
        bbox: tuple[float, float, float, float],
        width: int,
        height: int,
    ) -> object: ...


class ImageDecoder(Protocol):
    def decode(self, payload: bytes) -> DecodedImage: ...


class Detector(Protocol):
    def detect(self, image: DecodedImage, *, device: str) -> list[Detection]: ...


class Classifier(Protocol):
    def classify(self, crop: object, *, device: str) -> Classification: ...


class InferencePipeline:
    """CPU-safe orchestration around injected detector/classifier adapters.

    Counting rule: every accepted animal detection contributes at most one count
    within its frame. Ordered video inputs are processed in order, and each species
    keeps the maximum count observed in any supplied one-frame-per-second object.
    """

    def __init__(
        self,
        *,
        object_reader: ObjectReader,
        decoder: ImageDecoder,
        detector: Detector,
        classifier: Classifier,
        labels: tuple[str, ...],
        model_version: str,
        input_width: int,
        input_height: int,
        detection_threshold: float,
        classification_threshold: float,
        device: str = "cpu",
        animal_category: str = "1",
    ) -> None:
        if device not in {"cpu", "cuda", "mps"}:
            raise ValueError("device must be explicitly cpu, cuda, or mps")
        self.object_reader = object_reader
        self.decoder = decoder
        self.detector = detector
        self.classifier = classifier
        self.labels = labels
        self.model_version = model_version
        self.input_width = input_width
        self.input_height = input_height
        self.detection_threshold = detection_threshold
        self.classification_threshold = classification_threshold
        self.device = device
        self.animal_category = animal_category

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        result = self.infer_detailed(storage_uris)
        return InferenceResult(tag_counts=result.tag_counts, model_version=result.model_version)

    def infer_detailed(self, storage_uris: list[str]) -> StructuredInferenceResult:
        counts: Counter[str] = Counter()
        evidence: list[PredictionEvidence] = []
        for source_uri in storage_uris:
            frame_counts: Counter[str] = Counter()
            payload = self.object_reader.read(source_uri)
            try:
                image = self.decoder.decode(payload)
            except Exception as exc:
                raise ImageDecodeError(f"unable to decode image from {source_uri}") from exc
            for detection in self.detector.detect(image, device=self.device):
                if detection.category != self.animal_category:
                    continue
                if detection.confidence < self.detection_threshold:
                    continue
                _validate_bbox(detection.bbox)
                crop = image.crop_and_resize(
                    detection.bbox,
                    self.input_width,
                    self.input_height,
                )
                classification = self.classifier.classify(crop, device=self.device)
                if classification.confidence < self.classification_threshold:
                    continue
                if not 0 <= classification.class_index < len(self.labels):
                    raise InferenceExecutionError(
                        f"classifier returned class index {classification.class_index} outside manifest labels"
                    )
                species = self.labels[classification.class_index]
                frame_counts[species] += 1
                evidence.append(
                    PredictionEvidence(
                        source_uri=source_uri,
                        species=species,
                        detection_confidence=detection.confidence,
                        classification_confidence=classification.confidence,
                    )
                )
            for species, frame_count in frame_counts.items():
                counts[species] = max(counts[species], frame_count)
        return StructuredInferenceResult(
            tag_counts=dict(counts),
            model_version=self.model_version,
            evidence=tuple(evidence),
        )


def _validate_bbox(bbox: tuple[float, float, float, float]) -> None:
    x, y, width, height = bbox
    if width <= 0 or height <= 0 or min(x, y) < 0 or x + width > 1 or y + height > 1:
        raise InferenceExecutionError(f"detector returned invalid normalized bounding box: {bbox}")
