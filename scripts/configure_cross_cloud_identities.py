"""Safely stage and activate per-component Azure identities for AWS workloads.

The prepare phase creates/reuses Entra applications, creates short-lived client
credentials, and stores them directly in AWS Secrets Manager as ``AWSPENDING``.
Secrets are never printed or written to disk.  Azure principal IDs and AWS
secret version IDs are non-secret deployment metadata and are written under
the gitignored build directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

import boto3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA = PROJECT_ROOT / "build" / "cross-cloud-identities.json"
DEFAULT_AZURE_VARS = PROJECT_ROOT / "build" / "azure-component-identities.tfvars.json"
COMPONENTS = {
    "api": {
        "suffix": "aws-api",
        "secret_suffix": "azure-api-identity",
        "terraform_variable": "api_principal_id",
        "containers": ("media", "subscriptions", "delivery-ledger", "deletion-operations"),
    },
    "worker": {
        "suffix": "media-worker",
        "secret_suffix": "azure-worker-identity",
        "terraform_variable": "worker_principal_id",
        "containers": ("media",),
    },
    "notification": {
        "suffix": "notification",
        "secret_suffix": "azure-notification-identity",
        "terraform_variable": "notification_principal_id",
        "containers": ("subscriptions", "delivery-ledger"),
    },
}


class IdentitySetupError(RuntimeError):
    pass


def _command(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    raise IdentitySetupError(f"Required command is unavailable: {' or '.join(names)}")


def _az_json(az: str, arguments: Sequence[str], *, sensitive: bool = False) -> Any:
    if sensitive:
        print("+ az ad app credential reset [output captured securely]")
    else:
        print("+ az " + " ".join(arguments))
    result = subprocess.run(
        [az, *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        detail = result.stderr.strip() or "Azure CLI command failed"
        if detail.upper().startswith("ERROR:"):
            detail = detail[6:].strip()
        raise IdentitySetupError(detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise IdentitySetupError("Azure CLI returned invalid JSON") from error


def _single(items: object, description: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        raise IdentitySetupError(f"Azure CLI returned invalid {description} data")
    if not items:
        return None
    if len(items) > 1:
        raise IdentitySetupError(f"Multiple {description} objects matched; resolve duplicates before retrying")
    item = items[0]
    if not isinstance(item, dict):
        raise IdentitySetupError(f"Azure CLI returned invalid {description} data")
    return item


def _ensure_application(az: str, display_name: str) -> dict[str, Any]:
    existing = _single(
        _az_json(az, ["ad", "app", "list", "--display-name", display_name]),
        f"application {display_name}",
    )
    if existing is not None:
        return existing
    created = _az_json(
        az,
        ["ad", "app", "create", "--display-name", display_name, "--sign-in-audience", "AzureADMyOrg"],
    )
    if not isinstance(created, dict):
        raise IdentitySetupError(f"Azure CLI returned invalid application {display_name} data")
    return created


def _ensure_service_principal(az: str, app_id: str) -> dict[str, Any]:
    existing = _single(
        _az_json(az, ["ad", "sp", "list", "--filter", f"appId eq '{app_id}'"]),
        f"service principal {app_id}",
    )
    if existing is not None:
        return existing
    created = _az_json(az, ["ad", "sp", "create", "--id", app_id])
    if not isinstance(created, dict):
        raise IdentitySetupError(f"Azure CLI returned invalid service principal {app_id} data")
    return created


def _new_client_secret(az: str, app_id: str) -> str:
    credential = _az_json(
        az,
        [
            "ad",
            "app",
            "credential",
            "reset",
            "--id",
            app_id,
            "--append",
            "--display-name",
            f"pba-aws-{datetime.now(UTC):%Y%m%d}",
            "--years",
            "1",
        ],
        sensitive=True,
    )
    password = credential.get("password") if isinstance(credential, dict) else None
    if not isinstance(password, str) or not password:
        raise IdentitySetupError("Azure CLI did not return a client credential")
    return password


def _current_version(secret_client: Any, secret_id: str) -> str | None:
    description = secret_client.describe_secret(SecretId=secret_id)
    for version_id, stages in description.get("VersionIdsToStages", {}).items():
        if "AWSCURRENT" in stages:
            return version_id
    return None


def _stage_secret(
    secret_client: Any,
    *,
    secret_id: str,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> tuple[str, str | None]:
    previous = _current_version(secret_client, secret_id)
    response = secret_client.put_secret_value(
        SecretId=secret_id,
        SecretString=json.dumps(
            {"tenant_id": tenant_id, "client_id": client_id, "client_secret": client_secret},
            separators=(",", ":"),
        ),
        VersionStages=["AWSPENDING"],
    )
    version_id = response.get("VersionId")
    if not isinstance(version_id, str) or not version_id:
        raise IdentitySetupError(f"AWS did not return a version ID for {secret_id}")
    return version_id, previous


def _session(profile: str | None, region: str) -> Any:
    return boto3.Session(profile_name=profile, region_name=region)


def _metadata_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def prepare(args: argparse.Namespace) -> None:
    az = _command("az.cmd", "az") if os.name == "nt" else _command("az")
    account = _az_json(az, ["account", "show"])
    tenant_id = account.get("tenantId") if isinstance(account, dict) else None
    subscription_id = account.get("id") if isinstance(account, dict) else None
    if not isinstance(tenant_id, str) or not tenant_id:
        raise IdentitySetupError("Azure account has no tenant ID")

    secret_client = _session(args.aws_profile, args.aws_region).client("secretsmanager")
    prefix = f"{args.project_name}-{args.environment}"
    identities: dict[str, dict[str, Any]] = {}
    azure_vars: dict[str, str] = {}

    for component, config in COMPONENTS.items():
        display_name = f"{prefix}-{config['suffix']}"
        application = _ensure_application(az, display_name)
        app_id = application.get("appId")
        if not isinstance(app_id, str) or not app_id:
            raise IdentitySetupError(f"Application {display_name} has no appId")
        principal = _ensure_service_principal(az, app_id)
        principal_id = principal.get("id")
        if not isinstance(principal_id, str) or not principal_id:
            raise IdentitySetupError(f"Service principal {display_name} has no object ID")

        client_secret = _new_client_secret(az, app_id)
        secret_id = f"{prefix}/{config['secret_suffix']}"
        pending_version, previous_current = _stage_secret(
            secret_client,
            secret_id=secret_id,
            tenant_id=tenant_id,
            client_id=app_id,
            client_secret=client_secret,
        )
        identities[component] = {
            "display_name": display_name,
            "client_id": app_id,
            "principal_id": principal_id,
            "secret_id": secret_id,
            "pending_version_id": pending_version,
            "previous_current_version_id": previous_current,
        }
        azure_vars[str(config["terraform_variable"])] = principal_id
        print(f"Prepared {component}: principal={principal_id}, secret stage=AWSPENDING")

    metadata = {
        "schema_version": 1,
        "prepared_at": datetime.now(UTC).isoformat(),
        "tenant_id": tenant_id,
        "subscription_id": subscription_id,
        "aws_region": args.aws_region,
        "aws_profile": args.aws_profile,
        "identities": identities,
    }
    output = _metadata_path(args.output)
    azure_output = _metadata_path(args.azure_vars_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    azure_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    azure_output.write_text(json.dumps(azure_vars, indent=2) + "\n", encoding="utf-8")
    print(f"Non-secret deployment metadata: {output}")
    print(f"Azure Terraform variable file: {azure_output}")
    print("No live secret changed: all new credentials remain AWSPENDING.")


def activate(args: argparse.Namespace) -> None:
    if not args.confirm_rbac_applied:
        raise IdentitySetupError("Refusing activation without --confirm-rbac-applied")
    metadata_path = _metadata_path(args.metadata)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    secret_client = _session(metadata.get("aws_profile"), metadata["aws_region"]).client("secretsmanager")
    for component, identity in metadata["identities"].items():
        parameters: dict[str, Any] = {
            "SecretId": identity["secret_id"],
            "VersionStage": "AWSCURRENT",
            "MoveToVersionId": identity["pending_version_id"],
        }
        current = _current_version(secret_client, identity["secret_id"])
        if current and current != identity["pending_version_id"]:
            parameters["RemoveFromVersionId"] = current
        secret_client.update_secret_version_stage(**parameters)
        print(f"Activated {component} identity in {identity['secret_id']}")
    print("Credentials are active. Enable component identities in the reviewed AWS Terraform plan next.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="Create identities and stage credentials as AWSPENDING")
    prepare_parser.add_argument("--project-name", default="pacific-bioarchive")
    prepare_parser.add_argument("--environment", default="development")
    prepare_parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE"))
    prepare_parser.add_argument("--aws-region", default=os.environ.get("AWS_REGION", "ap-southeast-2"))
    prepare_parser.add_argument("--output", default=str(DEFAULT_METADATA))
    prepare_parser.add_argument("--azure-vars-output", default=str(DEFAULT_AZURE_VARS))
    prepare_parser.set_defaults(handler=prepare)

    activate_parser = commands.add_parser("activate", help="Promote staged credentials after Cosmos RBAC is applied")
    activate_parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    activate_parser.add_argument("--confirm-rbac-applied", action="store_true")
    activate_parser.set_defaults(handler=activate)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args)
        return 0
    except (IdentitySetupError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
