#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export UV_CACHE_DIR="$ROOT/.cache/uv"
PYTHON="$ROOT/.venv/bin/python"
mkdir -p offline/wheels
"$PYTHON" -m pip wheel --wheel-dir offline/wheels ./vendor/ACE-Step-1.5 'pip==26.3'
"$PYTHON" -m pip install --no-index --find-links offline/wheels ace-step 'pip==26.3'
"$PYTHON" -m pip check
"$PYTHON" -m pip freeze --all > offline/requirements.txt
"$PYTHON" -c 'import json, platform, subprocess; from pathlib import Path; Path("offline/build.json").write_text(json.dumps({"python": platform.python_version(), "architecture": platform.machine(), "upstream_commit": subprocess.check_output(["git", "-C", "vendor/ACE-Step-1.5", "rev-parse", "HEAD"], text=True).strip(), "model_revision": "19671f406d603126926c1b7e2adc169acbcade22"}, indent=2) + "\n")'