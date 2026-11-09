#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo 'This bundle requires an Apple Silicon Mac.' >&2
    exit 1
fi
if [[ -f transfer.sha256 ]]; then
    shasum -a 256 -c transfer.sha256
    while IFS= read -r archive; do
        if [[ -z "$archive" || "$archive" == */* || "$archive" != *.tar.gz ]]; then
            echo 'Invalid archive name in transfer list.' >&2
            exit 1
        fi
        marker=".cache/unpacked-$archive"
        if [[ ! -f "$marker" ]]; then
            mkdir -p .cache
            cat "$archive".part* | tar -xzf -
            touch "$marker"
        fi
    done < archives.txt
fi
PYTHON="$ROOT/runtime/cpython-3.12-macos-aarch64-none/bin/python3.12"
if [[ ! -x "$PYTHON" ]]; then
    echo 'Bundled Python is missing or not executable. Download every archive part and preserve executable permissions.' >&2
    exit 1
fi
"$PYTHON" - "$ROOT" <<'PY'
import hashlib
import json
import platform
import sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root / 'bundle-manifest.json').read_text())
actual = tuple(map(int, platform.mac_ver()[0].split('.')[:2]))
minimum = tuple(map(int, manifest['build']['minimum_macos'].split('.')))
if actual < minimum:
    raise SystemExit(f"macOS {manifest['build']['minimum_macos']} or newer required; found {platform.mac_ver()[0]}")
for entry in manifest['files']:
    path = root / entry['path']
    if not path.is_file() or path.stat().st_size != entry['size']:
        raise SystemExit(f"Missing or incomplete bundle file: {entry['path']}")
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != entry['sha256']:
        raise SystemExit(f"Bundle checksum mismatch: {entry['path']}")
print('Bundle integrity and macOS compatibility checks passed.')
PY
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CONFIG_FILE=/dev/null
export PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH
STAMP="$(shasum -a 256 offline/requirements.lock | awk '{print $1}'):$ROOT"
if [[ ! -x .venv/bin/python || ! -f .venv/.bundle-installed || "$(cat .venv/.bundle-installed)" != "$STAMP" ]]; then
    "$PYTHON" -m venv --clear --without-pip .venv
    PYTHONPATH="$ROOT/offline/wheels/pip-26.3-py3-none-any.whl" .venv/bin/python -m pip install \
        --no-index --find-links "$ROOT/offline/wheels" --require-hashes --no-compile -r offline/requirements.lock
    .venv/bin/python -m pip check
    printf '%s' "$STAMP" > .venv/.bundle-installed
fi
echo 'Ready. Use bash generate.sh --help or bash serve.sh --port 8001.'