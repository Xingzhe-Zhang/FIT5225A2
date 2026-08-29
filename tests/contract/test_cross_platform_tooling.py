from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def test_project_scripts_do_not_pin_a_local_interpreter() -> None:
    forbidden = ("." + "venv", "5225" + "A2", "5225" + "cloud", "/Users/")
    windows_absolute_path = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
    for path in SCRIPTS.iterdir():
        if path.suffix not in {".py", ".ps1", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{path.name}: {marker}"
        assert windows_absolute_path.search(text) is None, path.name


def test_windows_and_macos_entrypoints_share_the_python_task_runner() -> None:
    names = (
        "bootstrap",
        "start-local",
        "test-backend",
        "test-contracts",
        "test-frontend",
        "validate-infra",
        "build-aws-api-package",
        "build-aws-worker-package",
        "stage-azure-function",
        "build-push-aws-worker-image",
        "lock-terraform-providers",
    )
    for name in names:
        for suffix in ("ps1", "sh"):
            path = SCRIPTS / f"{name}.{suffix}"
            assert path.is_file(), path
            assert "project_tasks.py" in path.read_text(encoding="utf-8")


def test_environment_check_covers_the_required_toolchain() -> None:
    checker = (SCRIPTS / "check-environment.py").read_text(encoding="utf-8")
    assert "sys.executable" in checker
    for tool in ("Node.js", "npm", "Terraform", "Docker", "AWS CLI", "Azure CLI", "Azure Functions Core Tools"):
        assert tool in checker


def test_lambda_and_worker_builds_target_linux_amd64() -> None:
    tasks = (SCRIPTS / "project_tasks.py").read_text(encoding="utf-8")
    aws_stack = (ROOT / "infra" / "aws" / "main.tf").read_text(encoding="utf-8")
    assert 'LAMBDA_CONTAINER_PLATFORM = "linux/amd64"' in tasks
    assert 'LAMBDA_WHEEL_PLATFORM = "manylinux2014_x86_64"' in tasks
    assert 'architectures    = ["x86_64"]' in aws_stack


def test_terraform_lock_command_covers_supported_developer_platforms() -> None:
    tasks = (SCRIPTS / "project_tasks.py").read_text(encoding="utf-8")
    for platform in ("windows_amd64", "darwin_amd64", "darwin_arm64", "linux_amd64"):
        assert platform in tasks
    for cloud in ("aws", "azure"):
        lockfile = (ROOT / "infra" / cloud / ".terraform.lock.hcl").read_text(encoding="utf-8")
        assert lockfile.count('"h1:') >= 4


def test_sensitive_local_and_terraform_files_are_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in (".env", ".env.local", "terraform.tfvars", "*.tfvars", "*.tfstate", "*.tfstate.*"):
        assert entry in gitignore


def test_azure_function_package_excludes_local_and_large_artifacts() -> None:
    ignored = (ROOT / ".funcignore").read_text(encoding="utf-8").splitlines()
    for entry in ("build/", "frontend/", "infra/", "models/", "scripts/", "tests/", "tmp/"):
        assert entry in ignored

    tasks = (SCRIPTS / "project_tasks.py").read_text(encoding="utf-8")
    assert 'copy_tree(PROJECT_ROOT / "backend", stage / "backend")' in tasks
    assert 'for name in ("host.json", "function_app.py", "requirements.txt")' in tasks
