from __future__ import annotations

import hashlib
import io
import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Callable, Protocol
from urllib.parse import urlparse

from PIL import Image

from backend.common.providers.interfaces import InferenceResult, ObjectStorage

if TYPE_CHECKING:
    from backend.tagging.inference.manifest import LoadedModelBundle


class WildlifeRuntime(Protocol):
    def infer(self, inputs: list[tuple[str, bytes]]) -> InferenceResult: ...


class LocalWildlifeInferenceService:
    """Read storage objects and run one lazily loaded local wildlife model."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        model_dir: Path,
        device: str = "auto",
        detection_threshold: float = 0.05,
        classification_threshold: float = 0.5,
        runtime_factory: Callable[[], WildlifeRuntime] | None = None,
    ) -> None:
        self._storage = storage
        self._model_dir = Path(model_dir).expanduser().resolve()
        self._device = device
        self._detection_threshold = detection_threshold
        self._classification_threshold = classification_threshold
        self._runtime_factory = runtime_factory
        self._runtime: WildlifeRuntime | None = None

    @classmethod
    def from_manifest_bundle(
        cls,
        *,
        storage: ObjectStorage,
        bundle: "LoadedModelBundle",
    ) -> "LocalWildlifeInferenceService":
        """Build the production inference service from a validated manifest bundle."""

        return cls(
            storage=storage,
            model_dir=bundle.detector_path.parent,
            device=bundle.device,
            detection_threshold=bundle.detection_threshold,
            classification_threshold=bundle.classification_threshold,
            runtime_factory=lambda: TorchWildlifeRuntime(
                model_dir=bundle.detector_path.parent,
                device=bundle.device,
                detection_threshold=bundle.detection_threshold,
                classification_threshold=bundle.classification_threshold,
                detector_path=bundle.detector_path,
                classifier_path=bundle.classifier_path,
                labels_path=bundle.labels_path,
                model_version=bundle.model_version,
                input_width=bundle.input_width,
                input_height=bundle.input_height,
            ),
        )

    def infer(self, storage_uris: list[str]) -> InferenceResult:
        if not storage_uris:
            raise ValueError("at least one inference object is required")
        inputs = [(uri, self._storage.get_bytes(_object_key(uri))) for uri in storage_uris]
        return self._get_runtime().infer(inputs)

    def _get_runtime(self) -> WildlifeRuntime:
        if self._runtime is None:
            self._runtime = (
                self._runtime_factory()
                if self._runtime_factory is not None
                else TorchWildlifeRuntime(
                    model_dir=self._model_dir,
                    device=self._device,
                    detection_threshold=self._detection_threshold,
                    classification_threshold=self._classification_threshold,
                )
            )
        return self._runtime


class TorchWildlifeRuntime:
    """MegaDetector followed by the supplied 46-class SpeciesNet model.

    Heavy ML imports and model loading are delayed until the first inference.
    The classifier remains cached for subsequent uploads and temporary queries.
    """

    def __init__(
        self,
        *,
        model_dir: Path,
        device: str,
        detection_threshold: float,
        classification_threshold: float,
        detector_path: Path | None = None,
        classifier_path: Path | None = None,
        labels_path: Path | None = None,
        model_version: str | None = None,
        input_width: int = 480,
        input_height: int = 480,
    ) -> None:
        self._model_dir = model_dir
        self._detection_threshold = detection_threshold
        self._classification_threshold = classification_threshold
        self._detector_path = detector_path or model_dir / "mdv5a.pt"
        self._classifier_path = classifier_path or model_dir / "model.pt"
        self._labels_path = labels_path or model_dir / "labels.txt"
        self._input_width = input_width
        self._input_height = input_height
        missing = [path.name for path in (self._detector_path, self._classifier_path, self._labels_path) if not path.is_file()]
        if missing:
            raise ValueError(f"local ML model directory is missing: {', '.join(missing)}")

        try:
            import numpy as np
            import torch
        except ImportError as error:
            raise RuntimeError(
                "Local ML dependencies are missing; install the project 'ml' optional dependencies"
            ) from error

        try:
            from megadetector.detection import run_detector_batch
        except ModuleNotFoundError:
            # MegaDetector 5.0.4 publishes these modules at the package root;
            # later 5.x releases use the ``megadetector`` namespace.
            try:
                from detection import run_detector_batch
            except ImportError as error:
                raise RuntimeError(
                    "MegaDetector is installed but its detection runtime cannot be imported"
                ) from error

        self._np = np
        self._torch = torch
        self._run_detector_batch = run_detector_batch
        self._device = _resolve_device(torch, device)
        # The supplied file is a trusted local course artifact and requires onnx2torch while unpickling.
        self._classifier = torch.load(
            self._classifier_path,
            map_location=self._device,
            weights_only=False,
        )
        self._classifier.eval()
        self._classifier.to(self._device)
        self._labels = _load_labels(self._labels_path)
        self._inference_lock = Lock()
        if model_version is None:
            digest = hashlib.sha256(self._classifier_path.read_bytes()).hexdigest()[:12]
            model_version = f"speciesnet-{digest}"
        self._model_version = model_version

    def infer(self, inputs: list[tuple[str, bytes]]) -> InferenceResult:
        # The detector and the process-wide compatibility environment are not
        # safe to mutate concurrently.
        with self._inference_lock:
            return self._infer_serialized(inputs)

    def _infer_serialized(self, inputs: list[tuple[str, bytes]]) -> InferenceResult:
        with tempfile.TemporaryDirectory(prefix="pba-inference-") as directory:
            root = Path(directory)
            paths: list[Path] = []
            sources: dict[str, bytes] = {}
            for index, (uri, payload) in enumerate(inputs):
                suffix = Path(urlparse(uri).path).suffix.casefold()
                suffix = suffix if suffix in {".jpg", ".jpeg", ".png"} else ".jpg"
                path = root / f"input-{index:06d}{suffix}"
                path.write_bytes(payload)
                paths.append(path)
                sources[str(path)] = payload

            # PyTorch 2.6+ defaults to weights-only loading. MegaDetector 5.0.4
            # does not pass weights_only=False for its trusted local checkpoint.
            with _temporary_environment(
                TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1",
                YOLO_CONFIG_DIR=str(root),
            ):
                detections = self._run_detector_batch.load_and_run_detector_batch(
                    model_file=str(self._detector_path),
                    image_file_names=[str(path) for path in paths],
                )
            counts: Counter[str] = Counter()
            for entry in detections:
                image_path = str(entry["file"])
                payload = sources.get(image_path)
                if payload is None:
                    raise RuntimeError("MegaDetector returned an unknown input path")
                with Image.open(io.BytesIO(payload)) as opened:
                    image = opened.convert("RGB")
                    width, height = image.size
                    for detection in entry.get("detections", []):
                        if detection.get("category") != "1":
                            continue
                        if float(detection.get("conf", 0)) < self._detection_threshold:
                            continue
                        crop = _crop(image, width, height, detection.get("bbox"))
                        classification = self._classify(crop)
                        if classification is not None:
                            counts[classification] += 1
            return InferenceResult(tag_counts=dict(counts), model_version=self._model_version)

    def _classify(self, image: Image.Image) -> str | None:
        resized = image.resize(
            (self._input_width, self._input_height),
            Image.Resampling.BILINEAR,
        )
        values = self._np.asarray(resized, dtype=self._np.float32) / 255.0
        tensor = self._torch.from_numpy(values).unsqueeze(0).to(self._device)
        with self._torch.no_grad():
            logits = self._classifier(tensor)
            probabilities = self._torch.softmax(logits, dim=1)[0]
            confidence, index = self._torch.max(probabilities, dim=0)
        if float(confidence.item()) < self._classification_threshold:
            return None
        class_index = int(index.item())
        if not 0 <= class_index < len(self._labels):
            raise RuntimeError("classifier returned an out-of-range class index")
        return self._labels[class_index]


def _object_key(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError("inference URI must identify an S3 object")
    return parsed.path.lstrip("/")


def _resolve_device(torch: object, configured: str) -> str:
    if configured != "auto":
        return configured
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_labels(path: Path) -> tuple[str, ...]:
    labels: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        parts = raw_line.strip().split(";")
        if len(parts) < 6 or not parts[4].strip():
            raise ValueError("labels.txt contains an invalid taxonomy row")
        genus = parts[4].strip().capitalize()
        species = parts[5].strip()
        labels.append(f"{genus}_{species}" if species else genus)
    if not labels or len(labels) != len(set(labels)):
        raise ValueError("labels.txt must define unique species labels")
    return tuple(labels)


def _crop(image: Image.Image, width: int, height: int, raw_bbox: object) -> Image.Image:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        raise RuntimeError("MegaDetector returned an invalid bounding box")
    x, y, box_width, box_height = (float(value) for value in raw_bbox)
    if min(x, y, box_width, box_height) < 0 or x + box_width > 1 or y + box_height > 1:
        raise RuntimeError("MegaDetector returned an out-of-bounds bounding box")
    left = max(0, int(x * width))
    top = max(0, int(y * height))
    right = min(width, max(left + 1, int((x + box_width) * width)))
    bottom = min(height, max(top + 1, int((y + box_height) * height)))
    return image.crop((left, top, right, bottom))


@contextmanager
def _temporary_environment(**overrides: str):
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
