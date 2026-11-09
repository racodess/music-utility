#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
    echo 'Native setup requires an Apple Silicon Mac.' >&2
    exit 1
fi
RUNTIME="$ROOT/runtime/cpython-3.12.16-macos-aarch64-none"
ALIAS="$ROOT/runtime/cpython-3.12-macos-aarch64-none"
if [[ ! -x "$RUNTIME/bin/python3.12" ]]; then
    echo 'The copied standalone Python runtime is missing or not executable.' >&2
    exit 1
fi
if [[ -L "$ALIAS" ]]; then
    rm "$ALIAS"
elif [[ -e "$ALIAS" ]]; then
    echo 'Expected a Python runtime symlink; refusing to replace an existing directory or file.' >&2
    exit 1
fi
ln -s cpython-3.12.16-macos-aarch64-none "$ALIAS"
rm -f "$ROOT/.venv/.bundle-installed"
exec /bin/bash "$ROOT/setup.sh"