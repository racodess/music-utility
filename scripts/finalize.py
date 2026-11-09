import hashlib
import importlib.metadata
import json
import re
import subprocess
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    wheels = {}
    minimum_os = (11, 0)
    for path in (ROOT / "offline/wheels").glob("*.whl"):
        name, version, _, tags = parse_wheel_filename(path.name)
        wheels.setdefault((name, str(version)), []).append(path)
        for tag in tags:
            match = re.match(r"macosx_(\d+)_(\d+)_", tag.platform)
            if match:
                minimum_os = max(minimum_os, tuple(map(int, match.groups())))
    requirements = []
    licenses = []
    for distribution in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = distribution.metadata["Name"]
        candidates = wheels.get((canonicalize_name(name), distribution.version), [])
        if not candidates:
            raise RuntimeError(f"No offline wheel for {name}=={distribution.version}")
        hashes = " ".join(f"--hash=sha256:{sha256(path)}" for path in candidates)
        requirements.append(f"{name}=={distribution.version} {hashes}")
        licenses.append({"name": name, "version": distribution.version,
                         "license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License"),
                         "license_files": [str(path) for path in distribution.files or []
                                           if "license" in str(path).lower() or "notice" in str(path).lower()]})
    (ROOT / "offline/requirements.lock").write_text("\n".join(requirements) + "\n")
    (ROOT / "offline/licenses.json").write_text(json.dumps(licenses, indent=2) + "\n")
    patch = subprocess.check_output(["git", "-C", str(ROOT / "vendor/ACE-Step-1.5"), "diff"], text=True)
    (ROOT / "offline/upstream.patch").write_text(patch)
    build_path = ROOT / "offline/build.json"
    build = json.loads(build_path.read_text())
    build.update(minimum_macos=".".join(map(str, minimum_os)),
                 upstream_patch_sha256=sha256(ROOT / "offline/upstream.patch"),
                 backend={"dit": "mps-float16", "lm": "mlx", "vae": "mlx"})
    build_path.write_text(json.dumps(build, indent=2) + "\n")
    files = []
    for directory in ("runtime", "offline", "checkpoints"):
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or {".cache", "__pycache__"}.intersection(path.relative_to(ROOT).parts):
                continue
            files.append({"path": str(path.relative_to(ROOT)), "size": path.stat().st_size, "sha256": sha256(path)})
    (ROOT / "bundle-manifest.json").write_text(json.dumps({"build": build, "files": files}, indent=2) + "\n")
    print(f"Locked {len(requirements)} packages; hashed {len(files)} runtime, dependency, and model files.")


if __name__ == "__main__":
    main()