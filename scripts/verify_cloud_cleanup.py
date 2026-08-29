"""Run an isolated, destructive cleanup smoke test against the deployed clouds."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import boto3
from azure.cosmos import CosmosClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.aws_api.management.deletion import CrossCloudDeleteService
from backend.aws_api.media.s3_storage import S3Storage
from backend.azure_api.management.service import SignedUrlNormalizer
from backend.azure_api.media.cosmos_repository import CosmosPagedMediaRepository
from backend.azure_api.operations.cosmos import CosmosDeletionOperationStore
from backend.common.azure_cosmos_credential import cosmos_credential_from_secret_string
from backend.common.contracts.models import MediaRecord


AWS_STATE = PROJECT_ROOT / "infra" / "aws" / "terraform.tfstate"
AZURE_STATE = PROJECT_ROOT / "infra" / "azure" / "terraform.tfstate"


class SmokeError(RuntimeError):
    pass


class _Clock:
    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(UTC)


class _Ids:
    @staticmethod
    def new_uuid() -> UUID:
        return uuid4()


def _terraform_output(path: Path, name: str) -> Any:
    state = json.loads(path.read_text(encoding="utf-8"))
    try:
        return state["outputs"][name]["value"]
    except (KeyError, TypeError) as error:
        raise SmokeError(f"Terraform state has no output {name}: {path}") from error


def _remaining_versions(client: Any, bucket: str, prefixes: list[str]) -> list[dict[str, str]]:
    remaining: list[dict[str, str]] = []
    paginator = client.get_paginator("list_object_versions")
    for prefix in prefixes:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for collection in ("Versions", "DeleteMarkers"):
                remaining.extend(
                    {"Key": str(item["Key"]), "VersionId": str(item["VersionId"])}
                    for item in page.get(collection, [])
                )
    return remaining


def _delete_operation(container: Any, owner_sub: str, storage_uri: str) -> None:
    item_id = CosmosDeletionOperationStore._id(owner_sub, storage_uri)
    try:
        container.delete_item(item=item_id, partition_key=owner_sub)
    except Exception as error:
        if getattr(error, "status_code", None) != 404:
            raise


def run_smoke(args: argparse.Namespace) -> None:
    if not args.confirm_live_cleanup_smoke:
        raise SmokeError("Refusing live mutation without --confirm-live-cleanup-smoke")
    if args.frame_count < 1001:
        raise SmokeError("frame-count must be at least 1001 to exercise S3 batching")

    bucket = str(_terraform_output(AWS_STATE, "media_bucket"))
    secret_id = str(_terraform_output(AWS_STATE, "azure_worker_identity_secret_arn"))
    cosmos_endpoint = str(_terraform_output(AZURE_STATE, "cosmos_endpoint"))
    session = boto3.Session(profile_name=args.aws_profile, region_name=args.aws_region)
    identity = session.client("sts").get_caller_identity()
    if not bucket.endswith(str(identity["Account"])):
        raise SmokeError("AWS identity does not own the Terraform media bucket")

    s3_client = session.client("s3")
    versioning = s3_client.get_bucket_versioning(Bucket=bucket).get("Status")
    if versioning != "Enabled":
        raise SmokeError(f"S3 versioning must be Enabled, got {versioning!r}")

    secret = session.client("secretsmanager").get_secret_value(SecretId=secret_id)
    credential = cosmos_credential_from_secret_string(secret.get("SecretString"))
    cosmos = CosmosClient(cosmos_endpoint, credential=credential)
    database = cosmos.get_database_client(args.cosmos_database)
    media_container = database.get_container_client(args.media_container)
    operations_container = database.get_container_client(args.operations_container)
    repository = CosmosPagedMediaRepository(media_container)
    operations = CosmosDeletionOperationStore(operations_container)
    storage = S3Storage(s3_client, bucket)

    media_id = uuid4()
    owner_sub = f"__cleanup_smoke__:{media_id}"
    checksum = media_id.hex * 2
    file_name = "failed-smoke.jpg"
    original_key = f"originals/{media_id}/{checksum}/{file_name}"
    derived_prefix = f"derived/{media_id}/{checksum}/"
    thumbnail_key = f"{derived_prefix}thumbnail.jpg"
    frame_keys = [f"{derived_prefix}frames/{index:04d}.jpg" for index in range(args.frame_count)]
    quarantine_key = f"quarantine/{media_id}/{checksum}/{file_name}"
    legacy_quarantine_key = f"quarantine/{checksum}/{file_name}"
    all_keys = [original_key, thumbnail_key, quarantine_key, legacy_quarantine_key, *frame_keys]
    prefixes = [
        f"originals/{media_id}/",
        f"derived/{media_id}/",
        f"quarantine/{media_id}/",
        f"quarantine/{checksum}/",
    ]
    storage_uri = f"s3://{bucket}/{original_key}"

    print(f"AWS account: {identity['Account']}")
    print(f"Isolated media ID: {media_id}")
    print(f"Creating {len(all_keys)} tiny versioned objects...")
    try:
        reservation = repository.reserve_upload(owner_sub, checksum, media_id)
        if not reservation.created:
            raise SmokeError("Random smoke reservation unexpectedly already exists")
        now = datetime.now(UTC)
        repository.upsert(
            MediaRecord(
                media_id=media_id,
                owner_sub=owner_sub,
                sha256=checksum,
                file_name=file_name,
                media_type="image",
                original_storage_uri=storage_uri,
                thumbnail_storage_uri=f"s3://{bucket}/{thumbnail_key}",
                tag_counts={},
                manual_tags=[],
                model_version="cleanup-smoke-v1",
                status="failed",
                failure_code="SMOKE_FAILURE",
                failure_message="Synthetic failed record for deployment cleanup verification",
                created_at=now,
                updated_at=now,
            )
        )

        def put(key: str) -> None:
            s3_client.put_object(Bucket=bucket, Key=key, Body=b"x", ContentType="application/octet-stream")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            list(executor.map(put, all_keys))
        # Ensure at least one object has historical versions before deletion.
        put(original_key)

        service = CrossCloudDeleteService(
            repository=repository,
            storage=storage,
            operations=operations,
            clock=_Clock(),
            ids=_Ids(),
            normalizer=SignedUrlNormalizer(
                download_base_url=f"https://{bucket}.s3.{args.aws_region}.amazonaws.com",
                bucket_name=bucket,
            ),
        )
        outcome = service.delete_by_id(owner_sub=owner_sub, media_id=media_id)
        if outcome.status != "deleted":
            raise SmokeError(f"Deletion returned {outcome.status}: {outcome.error}")
        if repository.get(owner_sub, media_id) is not None:
            raise SmokeError("Media record remained after deletion")
        remaining = _remaining_versions(s3_client, bucket, prefixes)
        if remaining:
            raise SmokeError(f"S3 retained {len(remaining)} versions or delete markers")

        replacement_id = uuid4()
        replacement = repository.reserve_upload(owner_sub, checksum, replacement_id)
        if not replacement.created or replacement.media_id != replacement_id:
            raise SmokeError("Checksum reservation was not reusable after deletion")
        repository.release_upload_reservation(owner_sub, checksum, replacement_id)
        _delete_operation(operations_container, owner_sub, storage_uri)
        print("PASS: failed media record and checksum reservation were deleted")
        print("PASS: quarantine/original/thumbnail and >1000 derived objects were deleted")
        print("PASS: no S3 object version or delete marker remains")
        print("PASS: the same checksum can be reserved again")
    finally:
        # All prefixes are UUID/checksum isolated. This cleanup is safe to run
        # even after a partially failed smoke test.
        current_keys = sorted({key for prefix in prefixes for key in storage.list_keys(prefix)})
        storage.delete_keys(sorted(set(all_keys) | set(current_keys)))
        try:
            repository.delete(owner_sub, media_id)
        except Exception:
            pass
        repository.release_upload_reservation(owner_sub, checksum)
        _delete_operation(operations_container, owner_sub, storage_uri)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--aws-profile", default="pba-team")
    result.add_argument("--aws-region", default="ap-southeast-2")
    result.add_argument("--cosmos-database", default="bioarchive")
    result.add_argument("--media-container", default="media")
    result.add_argument("--operations-container", default="deletion-operations")
    result.add_argument("--frame-count", type=int, default=1001)
    result.add_argument("--workers", type=int, default=16)
    result.add_argument("--confirm-live-cleanup-smoke", action="store_true")
    return result


def main() -> int:
    try:
        run_smoke(parser().parse_args())
        return 0
    except (SmokeError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
