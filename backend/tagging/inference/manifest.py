from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import unquote, urlparse


class ManifestError(ValueError):
    """Base class for deterministic model loading failures."""


class ManifestValidationError(ManifestError):
    """The manifest is syntactically valid JSON but incompatible."""


class ArtifactChecksumError(ManifestError):
    """An artifact did not match the digest declared by the manifest."""


class ArtifactReader(Protocol):
    def read(self, uri: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ArtifactReference:
    uri: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ModelManifest:
    schema_version: str
    model_version: str
    detector: ArtifactReference
    classifier: ArtifactReference
    labels: ArtifactReference
    input_width: int
    input_height: int
    detection_threshold: float
    classification_threshold: float

    @classmethod
    def parse(cls, payload: bytes) -> "ModelManifest":
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise TypeError("root must be an object")
            schema_version = value["schema_version"]
            model_version = value["model_version"]
            input_config = value["input"]
            thresholds = value["thresholds"]
            manifest = cls(
                schema_version=schema_version,
                model_version=model_version,
                detector=_artifact(value["detector"], "detector"),
                classifier=_artifact(value["classifier"], "classifier"),
                labels=_artifact(value["labels"], "labels"),
                input_width=input_config["width"],
                input_height=input_config["height"],
                detection_threshold=thresholds["detection"],
                classification_threshold=thresholds["classification"],
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ManifestValidationError(f"invalid model manifest: {exc}") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ManifestValidationError("unsupported manifest schema version")
        if re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", self.model_version) is None:
            raise ManifestValidationError("model_version must be a semantic version")
        if isinstance(self.input_width, bool) or not isinstance(self.input_width, int) or self.input_width < 1:
            raise ManifestValidationError("input width must be a positive integer")
        if isinstance(self.input_height, bool) or not isinstance(self.input_height, int) or self.input_height < 1:
            raise ManifestValidationError("input height must be a positive integer")
        for name, threshold in (
            ("detection", self.detection_threshold),
            ("classification", self.classification_threshold),
        ):
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
                raise ManifestValidationError(f"{name} threshold must be between zero and one")


@dataclass(frozen=True, slots=True)
class LoadedModelBundle:
    model_version: str
    detector_path: Path
    classifier_path: Path
    labels_path: Path
    labels: tuple[str, ...]
    input_width: int
    input_height: int
    detection_threshold: float
    classification_threshold: float
    device: str


class LocalArtifactReader:
    def read(self, uri: str) -> bytes:
        if _is_windows_path(uri):
            try:
                return Path(uri).read_bytes()
            except OSError as exc:
                raise ManifestError(f"artifact unavailable: {uri}") from exc
        parsed = urlparse(uri)
        if parsed.scheme not in {"", "file"}:
            raise ManifestValidationError(f"local reader cannot read URI scheme {parsed.scheme!r}")
        if parsed.scheme == "file":
            path_text = unquote(parsed.path)
            if parsed.netloc:
                path_text = f"//{parsed.netloc}{path_text}"
            if re.match(r"^/[A-Za-z]:/", path_text):
                path_text = path_text[1:]
            path = Path(path_text)
        else:
            path = Path(uri)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ManifestError(f"artifact unavailable: {uri}") from exc


class S3ArtifactReader:
    def __init__(self, client: object) -> None:
        self._client = client

    def read(self, uri: str) -> bytes:
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
            raise ManifestValidationError("S3 artifact URI must contain bucket and key")
        try:
            response = self._client.get_object(Bucket=parsed.netloc, Key=unquote(parsed.path.lstrip("/")))
            return bytes(response["Body"].read())
        except Exception as exc:
            raise ManifestError(f"S3 artifact unavailable: {uri}") from exc


class ManifestBundleLoader:
    def __init__(self, *, readers: Mapping[str, ArtifactReader], cache_dir: Path) -> None:
        self._readers = dict(readers)
        self._cache_dir = Path(cache_dir)

    def load(self, manifest_uri: str, *, device: str = "cpu") -> LoadedModelBundle:
        if device not in {"cpu", "cuda", "mps"}:
            raise ManifestValidationError("device must be explicitly cpu, cuda, or mps")
        manifest = ModelManifest.parse(self._read(manifest_uri))
        artifacts = {
            "detector": self._validated_bytes("detector", manifest.detector),
            "classifier": self._validated_bytes("classifier", manifest.classifier),
            "labels": self._validated_bytes("labels", manifest.labels),
        }
        cache_digest = hashlib.sha256(
            (manifest.model_version + "|" + "|".join(
                ref.sha256 for ref in (manifest.detector, manifest.classifier, manifest.labels)
            )).encode("ascii")
        ).hexdigest()
        cache_root = self._cache_dir / manifest.model_version / cache_digest
        paths = {name: cache_root / name for name in artifacts}
        cache_root.mkdir(parents=True, exist_ok=True)
        for name, path in paths.items():
            if not path.exists():
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(artifacts[name])
                temporary.replace(path)
        try:
            labels = tuple(
                label.strip()
                for label in artifacts["labels"].decode("utf-8").splitlines()
                if label.strip()
            )
        except UnicodeDecodeError as exc:
            raise ManifestValidationError("labels artifact must be UTF-8") from exc
        if not labels or len(labels) != len(set(labels)):
            raise ManifestValidationError("labels artifact must contain unique non-empty labels")
        return LoadedModelBundle(
            model_version=manifest.model_version,
            detector_path=paths["detector"],
            classifier_path=paths["classifier"],
            labels_path=paths["labels"],
            labels=labels,
            input_width=manifest.input_width,
            input_height=manifest.input_height,
            detection_threshold=float(manifest.detection_threshold),
            classification_threshold=float(manifest.classification_threshold),
            device=device,
        )

    def _read(self, uri: str) -> bytes:
        scheme = "file" if _is_windows_path(uri) else (urlparse(uri).scheme or "file")
        reader = self._readers.get(scheme)
        if reader is None:
            raise ManifestValidationError(f"unsupported artifact URI scheme: {scheme}")
        return reader.read(uri)

    def _validated_bytes(self, name: str, reference: ArtifactReference) -> bytes:
        payload = self._read(reference.uri)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != reference.sha256:
            raise ArtifactChecksumError(
                f"{name} checksum mismatch: expected {reference.sha256}, got {actual}"
            )
        return payload


def load_configured_bundle(
    loader: ManifestBundleLoader,
    environ: Mapping[str, str] | None = None,
) -> LoadedModelBundle:
    values = os.environ if environ is None else environ
    uri = values.get("MODEL_MANIFEST_URI", "").strip()
    if not uri:
        raise ManifestValidationError("MODEL_MANIFEST_URI is required")
    return loader.load(uri, device=values.get("MODEL_DEVICE", "cpu"))


def _artifact(value: object, name: str) -> ArtifactReference:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{name} must be an object")
    uri = value.get("uri")
    digest = value.get("sha256")
    if not isinstance(uri, str) or not uri.strip():
        raise ManifestValidationError(f"{name} URI is required")
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise ManifestValidationError(f"{name} SHA-256 must be lowercase hexadecimal")
    return ArtifactReference(uri=uri, sha256=digest)


def _is_windows_path(value: str) -> bool:
    return re.match(r"^[A-Za-z]:[\\/]", value) is not None
