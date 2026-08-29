from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Tool:
    name: str
    commands: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)


TOOLS = (
    Tool("Node.js", ("node",)),
    Tool("npm", ("npm.cmd", "npm") if platform.system() == "Windows" else ("npm",)),
    Tool("Terraform", ("terraform",), ("version",)),
    Tool("Docker", ("docker",), ("version", "--format", "client={{.Client.Version}} server={{.Server.Version}}")),
    Tool("AWS CLI", ("aws",)),
    Tool("Azure CLI", ("az.cmd", "az") if platform.system() == "Windows" else ("az",), ("version",)),
    Tool("Azure Functions Core Tools", ("func",)),
)


def resolve_command(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def compact_version(name: str, output: str) -> str:
    output = output.strip()
    if name == "Azure CLI":
        try:
            return str(json.loads(output)["azure-cli"])
        except (KeyError, TypeError, json.JSONDecodeError):
            pass
    return re.sub(r"\s+", " ", output)[:180]


def main() -> int:
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Python executable: {Path(sys.executable).resolve()}")
    print(f"Python version: {platform.python_version()}")

    failed = sys.version_info[:2] != (3, 12)
    if failed:
        print("[FAIL] Python 3.12 must be the currently active interpreter.")

    for tool in TOOLS:
        executable = resolve_command(tool.commands)
        if executable is None:
            failed = True
            print(f"[MISSING] {tool.name}")
            continue
        completed = subprocess.run(
            [executable, *tool.version_args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = completed.stdout or completed.stderr
        status = "OK" if completed.returncode == 0 else "FAIL"
        if completed.returncode != 0:
            failed = True
        print(f"[{status}] {tool.name}: {compact_version(tool.name, output)}")
        print(f"       path: {Path(executable).resolve()}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

