#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT"
export PYTHONNOUSERSITE=1
unset PYTHONHOME
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    echo 'Run bash setup.sh first.' >&2
    exit 1
fi
exec "$ROOT/.venv/bin/python" -m ace_step_local.api "$@"