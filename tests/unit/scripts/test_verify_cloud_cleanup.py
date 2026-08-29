from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import verify_cloud_cleanup as smoke


def test_terraform_output_reads_non_secret_output(tmp_path: Path) -> None:
    state = tmp_path / "terraform.tfstate"
    state.write_text(json.dumps({"outputs": {"media_bucket": {"value": "bucket-name"}}}), encoding="utf-8")

    assert smoke._terraform_output(state, "media_bucket") == "bucket-name"


def test_terraform_output_rejects_missing_output(tmp_path: Path) -> None:
    state = tmp_path / "terraform.tfstate"
    state.write_text(json.dumps({"outputs": {}}), encoding="utf-8")

    with pytest.raises(smoke.SmokeError, match="no output"):
        smoke._terraform_output(state, "media_bucket")


def test_live_smoke_requires_explicit_confirmation() -> None:
    args = smoke.parser().parse_args([])

    with pytest.raises(smoke.SmokeError, match="confirm-live-cleanup-smoke"):
        smoke.run_smoke(args)
