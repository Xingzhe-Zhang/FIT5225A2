from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from scripts import configure_cross_cloud_identities as identities


class FakeSecretsManager:
    def __init__(self, *, current: str | None = "current-version") -> None:
        self.current = current
        self.put_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []

    def describe_secret(self, **_kwargs: object) -> dict[str, object]:
        stages = {self.current: ["AWSCURRENT"]} if self.current else {}
        return {"VersionIdsToStages": stages}

    def put_secret_value(self, **kwargs: object) -> dict[str, str]:
        self.put_calls.append(kwargs)
        return {"VersionId": "pending-version"}

    def update_secret_version_stage(self, **kwargs: object) -> None:
        self.update_calls.append(kwargs)


def test_single_rejects_ambiguous_directory_matches() -> None:
    with pytest.raises(identities.IdentitySetupError, match="Multiple"):
        identities._single([{"id": "one"}, {"id": "two"}], "application")


def test_stage_secret_uses_pending_without_writing_credential_to_metadata() -> None:
    client = FakeSecretsManager()

    pending, previous = identities._stage_secret(
        client,
        secret_id="pba/worker",
        tenant_id="tenant",
        client_id="client",
        client_secret="top-secret",
    )

    assert pending == "pending-version"
    assert previous == "current-version"
    call = client.put_calls[0]
    assert call["VersionStages"] == ["AWSPENDING"]
    assert json.loads(str(call["SecretString"])) == {
        "tenant_id": "tenant",
        "client_id": "client",
        "client_secret": "top-secret",
    }


def test_activate_requires_explicit_rbac_confirmation(tmp_path: Path) -> None:
    with pytest.raises(identities.IdentitySetupError, match="confirm-rbac-applied"):
        identities.activate(
            Namespace(confirm_rbac_applied=False, metadata=str(tmp_path / "missing.json"))
        )


def test_activate_promotes_pending_and_removes_previous_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata_path = tmp_path / "identities.json"
    metadata_path.write_text(
        json.dumps(
            {
                "aws_profile": "pba-team",
                "aws_region": "ap-southeast-2",
                "identities": {
                    "worker": {
                        "secret_id": "pba/worker",
                        "pending_version_id": "pending-version",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    client = FakeSecretsManager()

    class FakeSession:
        def client(self, service: str) -> FakeSecretsManager:
            assert service == "secretsmanager"
            return client

    monkeypatch.setattr(identities, "_session", lambda *_args: FakeSession())

    identities.activate(Namespace(confirm_rbac_applied=True, metadata=str(metadata_path)))

    assert client.update_calls == [
        {
            "SecretId": "pba/worker",
            "VersionStage": "AWSCURRENT",
            "MoveToVersionId": "pending-version",
            "RemoveFromVersionId": "current-version",
        }
    ]
