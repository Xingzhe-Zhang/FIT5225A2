from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import NoReturn, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
BUILD_DIR = PROJECT_ROOT / "build"
LAMBDA_PYTHON_VERSION = "312"
LAMBDA_WHEEL_PLATFORM = "manylinux2014_x86_64"
LAMBDA_CONTAINER_PLATFORM = "linux/amd64"
TERRAFORM_PLATFORMS = ("windows_amd64", "darwin_amd64", "darwin_arm64", "linux_amd64")


class TaskError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise TaskError(message)


def require_python_312() -> None:
    if sys.version_info[:2] != (3, 12):
        fail(f"Python 3.12 is required; active interpreter is {sys.executable} ({sys.version.split()[0]}).")
    print(f"Python executable: {Path(sys.executable).resolve()}")


def command(*candidates: str) -> str:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    fail(f"Required command is unavailable: {' or '.join(candidates)}")


def npm_command() -> str:
    return command("npm.cmd", "npm") if os.name == "nt" else command("npm")


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path = PROJECT_ROOT,
    input_bytes: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> None:
    rendered = [str(item) for item in args]
    print(f"+ {' '.join(rendered)}")
    subprocess.run(rendered, cwd=cwd, input=input_bytes, env=environment, check=True)


def bootstrap() -> None:
    require_python_312()
    run([sys.executable, "-m", "pip", "install", "-e", f"{PROJECT_ROOT}[dev]"])
    run([sys.executable, "-m", "pip", "install", "-r", PROJECT_ROOT / "requirements-azure-functions.txt"])
    run([npm_command(), "ci"], cwd=PROJECT_ROOT / "frontend")


def test_backend() -> None:
    require_python_312()
    run([sys.executable, "-m", "pytest", PROJECT_ROOT / "tests" / "unit", "-q"])


def test_contracts() -> None:
    require_python_312()
    run([sys.executable, "-m", "pytest", PROJECT_ROOT / "tests" / "contract", "-q"])


def test_frontend() -> None:
    require_python_312()
    npm = npm_command()
    run([npm, "test", "--", "--run"], cwd=PROJECT_ROOT / "frontend")
    run([npm, "run", "build"], cwd=PROJECT_ROOT / "frontend")


def start_local() -> int:
    require_python_312()
    environment = os.environ.copy()
    environment["APP_ENV"] = "local"
    environment.setdefault("LOCAL_AUTH_SECRET", base64.b64encode(secrets.token_bytes(32)).decode("ascii"))
    process_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.aws_api.app:app",
            "--reload",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        **process_options,
    )
    frontend = subprocess.Popen(
        [npm_command(), "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173", "--strictPort"],
        cwd=PROJECT_ROOT / "frontend",
        env=environment,
        **process_options,
    )
    processes = (backend, frontend)
    try:
        while True:
            for process in processes:
                result = process.poll()
                if result is not None:
                    return result
            time.sleep(0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                    )
                else:
                    os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def clean_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def pip_install_for_lambda(requirement: Path, target: Path, *, require_hashes: bool = False) -> None:
    import certifi

    args: list[str | os.PathLike[str]] = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--ignore-installed",
        "--upgrade",
        "--no-compile",
        "--only-binary=:all:",
        "--cert",
        certifi.where(),
        "--platform",
        LAMBDA_WHEEL_PLATFORM,
        "--implementation",
        "cp",
        "--python-version",
        LAMBDA_PYTHON_VERSION,
    ]
    if require_hashes:
        args.append("--require-hashes")
    args.extend(["--requirement", requirement, "--target", target])
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pip-lambda-", dir=BUILD_DIR) as temporary:
        environment = os.environ.copy()
        for name in ("TEMP", "TMP", "TMPDIR"):
            environment[name] = temporary
        run(args, environment=environment)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def create_zip(source: Path, output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source).as_posix())
    print(f"Package: {output}")


def output_path(value: str | None, default_name: str) -> Path:
    if value is None:
        return BUILD_DIR / default_name
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def build_aws_api_package(output: str | None = None) -> None:
    require_python_312()
    stage = BUILD_DIR / "aws-api-stage"
    clean_directory(stage)
    requirements = PROJECT_ROOT / "backend" / "aws_api"
    pip_install_for_lambda(requirements / "requirements-lambda.lock", stage, require_hashes=True)
    pip_install_for_lambda(requirements / "requirements-lambda-cloud.txt", stage)
    copy_tree(PROJECT_ROOT / "backend", stage / "backend")
    copy_tree(PROJECT_ROOT / "contracts", stage / "contracts")
    shutil.copy2(requirements / "lambda_adapter.py", stage / "lambda_adapter.py")
    shutil.copy2(PROJECT_ROOT / "notification_adapter.py", stage / "notification_adapter.py")
    create_zip(stage, output_path(output, "aws-api.zip"))


def build_aws_worker_package(output: str | None = None) -> None:
    require_python_312()
    stage = BUILD_DIR / "aws-worker-stage"
    clean_directory(stage)
    requirements = PROJECT_ROOT / "backend" / "aws_api" / "requirements-worker.txt"
    pip_install_for_lambda(requirements, stage)
    copy_tree(PROJECT_ROOT / "backend", stage / "backend")
    shutil.copy2(PROJECT_ROOT / "worker_adapter.py", stage / "worker_adapter.py")
    create_zip(stage, output_path(output, "aws-worker.zip"))


def stage_azure_function(output: str | None = None) -> None:
    """Create a minimal publish directory without traversing local artifacts."""

    require_python_312()
    stage = output_path(output, "azure-function-app")
    clean_directory(stage)
    copy_tree(PROJECT_ROOT / "backend", stage / "backend")
    for name in ("host.json", "function_app.py", "requirements.txt"):
        shutil.copy2(PROJECT_ROOT / name, stage / name)
    print(f"Azure Function publish directory: {stage.resolve()}")


def prepare_worker_context(model_directory: Path) -> Path:
    required = ("mdv5a.pt", "model.pt", "labels.txt")
    for name in required:
        artifact = model_directory / name
        if not artifact.is_file():
            fail(f"Missing model artifact: {artifact}")
    context = BUILD_DIR / "aws-worker-image"
    clean_directory(context)
    copy_tree(PROJECT_ROOT / "backend", context / "backend")
    shutil.copy2(PROJECT_ROOT / "worker_adapter.py", context / "worker_adapter.py")
    deployment = PROJECT_ROOT / "deployment" / "aws-worker"
    shutil.copy2(deployment / "Dockerfile", context / "Dockerfile")
    shutil.copy2(deployment / "requirements-worker-container.txt", context / "requirements-worker-container.txt")
    models = context / "models"
    models.mkdir()
    for name in required:
        shutil.copy2(model_directory / name, models / name)
    return context


def build_push_worker_image(repository_uri: str, tag: str, model_directory: str | None) -> None:
    match = re.fullmatch(r"(?P<account>\d{12})\.dkr\.ecr\.(?P<region>[a-z0-9-]+)\.amazonaws\.com/(?P<repo>.+)", repository_uri)
    if match is None:
        fail(f"Invalid ECR repository URI: {repository_uri}")
    aws = command("aws")
    docker = command("docker")
    account = subprocess.check_output([aws, "sts", "get-caller-identity", "--query", "Account", "--output", "text"], text=True).strip()
    if account != match["account"]:
        fail(f"AWS CLI is using account {account}, but the ECR repository belongs to {match['account']}.")
    run([aws, "ecr", "describe-repositories", "--region", match["region"], "--registry-id", account, "--repository-names", match["repo"]])
    registry = f"{account}.dkr.ecr.{match['region']}.amazonaws.com"
    password = subprocess.check_output([aws, "ecr", "get-login-password", "--region", match["region"]])
    run([docker, "login", "--username", "AWS", "--password-stdin", registry], input_bytes=password)
    models = Path(model_directory).expanduser() if model_directory else PROJECT_ROOT / "models"
    if not models.is_absolute():
        models = PROJECT_ROOT / models
    context = prepare_worker_context(models.resolve())
    image = f"{repository_uri}:{tag}"
    run([
        docker,
        "buildx",
        "build",
        "--platform",
        LAMBDA_CONTAINER_PLATFORM,
        "--provenance=false",
        "--push",
        "--tag",
        image,
        context,
    ])
    digest = subprocess.check_output(
        [
            aws,
            "ecr",
            "describe-images",
            "--region",
            match["region"],
            "--registry-id",
            account,
            "--repository-name",
            match["repo"],
            "--image-ids",
            f"imageTag={tag}",
            "--query",
            "imageDetails[0].imageDigest",
            "--output",
            "text",
        ],
        text=True,
    ).strip()
    if re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None:
        fail("Unable to resolve the pushed ECR image digest")
    print(f"Worker image URI: {repository_uri}@{digest}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_release_manifest(bucket: str, version: str, model_directory: Path) -> dict[str, object]:
    artifacts = {
        "detector": model_directory / "mdv5a.pt",
        "classifier": model_directory / "model.pt",
        "labels": model_directory / "labels.txt",
    }
    for path in artifacts.values():
        if not path.is_file():
            fail(f"Missing model artifact: {path}")
    prefix = f"models/releases/{version}"
    return {
        "schema_version": "1.0",
        "model_version": version,
        **{
            name: {
                "uri": f"s3://{bucket}/{prefix}/{path.name}",
                "sha256": _sha256_file(path),
            }
            for name, path in artifacts.items()
        },
        "input": {"width": 480, "height": 480},
        "thresholds": {"detection": 0.05, "classification": 0.5},
    }


def publish_model_release(bucket: str, version: str, model_directory: str | None) -> None:
    require_python_312()
    if re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version) is None:
        fail("Model release version must be semantic versioning such as 1.0.0")
    if re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket) is None:
        fail("Invalid S3 bucket name")
    models = Path(model_directory).expanduser() if model_directory else PROJECT_ROOT / "models"
    if not models.is_absolute():
        models = PROJECT_ROOT / models
    models = models.resolve()
    manifest = _model_release_manifest(bucket, version, models)
    release_dir = BUILD_DIR / "model-releases" / version
    clean_directory(release_dir)
    manifest_path = release_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    aws = command("aws")
    prefix = f"models/releases/{version}"
    for filename in ("mdv5a.pt", "model.pt", "labels.txt"):
        run(
            [
                aws,
                "s3",
                "cp",
                models / filename,
                f"s3://{bucket}/{prefix}/{filename}",
                "--only-show-errors",
                "--cache-control",
                "public,max-age=31536000,immutable",
            ]
        )
    run(
        [
            aws,
            "s3",
            "cp",
            manifest_path,
            f"s3://{bucket}/{prefix}/manifest.json",
            "--only-show-errors",
            "--content-type",
            "application/json",
            "--cache-control",
            "no-cache",
        ]
    )
    print(f"Model manifest URI: s3://{bucket}/{prefix}/manifest.json")


def report_terraform_state() -> None:
    for cloud in ("aws", "azure"):
        stack = PROJECT_ROOT / "infra" / cloud
        backend_configured = any('backend "' in path.read_text(encoding="utf-8") for path in stack.glob("*.tf"))
        state_present = any(stack.glob("terraform.tfstate*"))
        print(f"Terraform {cloud}: backend_configured={backend_configured}, local_state_present={state_present}")
    print("Terraform validation never runs plan or apply. Confirm/import existing cloud state before deployment.")


def validate_infra(skip_build: bool = False) -> None:
    require_python_312()
    if not skip_build:
        build_aws_api_package()
        build_aws_worker_package()
    run([sys.executable, "-m", "pytest", PROJECT_ROOT / "tests" / "contract" / "test_terraform_static.py", "-q"])
    terraform = command("terraform")
    report_terraform_state()
    run([terraform, "fmt", "-check", "-recursive", PROJECT_ROOT / "infra"])
    for cloud in ("aws", "azure"):
        stack = PROJECT_ROOT / "infra" / cloud
        run([terraform, f"-chdir={stack}", "init", "-backend=false", "-input=false", "-lockfile=readonly"])
        run([terraform, f"-chdir={stack}", "validate"])


def lock_terraform_providers() -> None:
    terraform = command("terraform")
    platform_args = [f"-platform={platform_name}" for platform_name in TERRAFORM_PLATFORMS]
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terraform-lock-", dir=BUILD_DIR) as temporary:
        environment = os.environ.copy()
        for name in ("TEMP", "TMP", "TMPDIR"):
            environment[name] = temporary
        for cloud in ("aws", "azure"):
            stack = PROJECT_ROOT / "infra" / cloud
            run(
                [terraform, f"-chdir={stack}", "providers", "lock", *platform_args],
                environment=environment,
            )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Cross-platform Pacific BioArchive project tasks")
    commands = root.add_subparsers(dest="task", required=True)
    for name in ("bootstrap", "start-local", "test-backend", "test-contracts", "test-frontend", "lock-terraform-providers"):
        commands.add_parser(name)
    for name in ("build-aws-api", "build-aws-worker", "stage-azure-function"):
        build = commands.add_parser(name)
        build.add_argument("--output")
    validate = commands.add_parser("validate-infra")
    validate.add_argument("--skip-build", action="store_true")
    image = commands.add_parser("build-push-worker-image")
    image.add_argument("repository_uri")
    image.add_argument("--tag", default="ml-v1")
    image.add_argument("--model-directory")
    model_release = commands.add_parser("publish-model-release")
    model_release.add_argument("bucket")
    model_release.add_argument("version")
    model_release.add_argument("--model-directory")
    return root


def main() -> int:
    args = parser().parse_args()
    handlers = {
        "bootstrap": bootstrap,
        "test-backend": test_backend,
        "test-contracts": test_contracts,
        "test-frontend": test_frontend,
        "lock-terraform-providers": lock_terraform_providers,
    }
    try:
        if args.task == "start-local":
            return start_local()
        if args.task == "build-aws-api":
            build_aws_api_package(args.output)
        elif args.task == "build-aws-worker":
            build_aws_worker_package(args.output)
        elif args.task == "stage-azure-function":
            stage_azure_function(args.output)
        elif args.task == "validate-infra":
            validate_infra(args.skip_build)
        elif args.task == "build-push-worker-image":
            build_push_worker_image(args.repository_uri, args.tag, args.model_directory)
        elif args.task == "publish-model-release":
            publish_model_release(args.bucket, args.version, args.model_directory)
        else:
            handlers[args.task]()
        return 0
    except (TaskError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
