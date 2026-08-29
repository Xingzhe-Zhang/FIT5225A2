#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python_bin="$(command -v python || true)"
if [[ -z "${python_bin}" ]]; then
  echo "Activate a Python 3.12 environment before running this script." >&2
  exit 1
fi
exec "${python_bin}" "${script_dir}/project_tasks.py" test-contracts "$@"
