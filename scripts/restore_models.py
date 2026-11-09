import argparse
import hashlib
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCK_SIZE = 8 * 1024 * 1024
PART_SIZE = 500_000_000
MAX_FILE_SIZE = 2_000_000_000


def asset(root, name):
    base = (root / "checkpoints").resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError(f"Invalid checkpoint path: {name}")
    return path


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def matches(path, record):
    return (path.is_file() and path.stat().st_size == record["size"]
            and checksum(path) == record["sha256"])


def verify(path, record):
    if not matches(path, record):
        raise ValueError(f"Missing or corrupt model asset: {path}. Run git lfs pull and retry.")


def prepare(root, part_size=PART_SIZE, max_file_size=MAX_FILE_SIZE):
    records = json.loads((root / "offline/models.json").read_text())["files"]
    bundle_path = root / "bundle-manifest.json"
    if bundle_path.is_file():
        bundled = {entry["path"]: entry for entry in json.loads(bundle_path.read_text())["files"]}
        records = [
            {**bundled["checkpoints/" + entry["path"]], "path": entry["path"]}
            if entry["path"].endswith(".py") and "checkpoints/" + entry["path"] in bundled
            else entry
            for entry in records
        ]
    for record in records:
        verify(asset(root, record["path"]), record)
    split_files = []
    for record in records:
        source = asset(root, record["path"])
        if record["size"] <= max_file_size:
            continue
        parts = []
        with source.open("rb") as stream:
            remaining = record["size"]
            while remaining:
                name = f"{record['path']}.part{len(parts) + 1:03d}"
                destination = asset(root, name)
                size = min(part_size, remaining)
                digest = hashlib.sha256()
                with destination.open("wb") as output:
                    pending = size
                    while pending:
                        block = stream.read(min(BLOCK_SIZE, pending))
                        if not block:
                            raise ValueError(f"Model changed while splitting: {source}")
                        output.write(block)
                        digest.update(block)
                        pending -= len(block)
                parts.append({"path": name, "size": size, "sha256": digest.hexdigest()})
                remaining -= size
        split_files.append({**record, "parts": parts})
    manifest = {"maximum_part_bytes": part_size, "model_files": records, "files": split_files}
    (root / "offline/model-parts.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def restore(root):
    manifest = json.loads((root / "offline/model-parts.json").read_text())
    records = manifest["model_files"]
    originals = {record["path"]: record for record in records}
    for record in manifest["files"]:
        expected = originals[record["path"]]
        destination = asset(root, record["path"])
        if matches(destination, expected):
            continue
        for part in record["parts"]:
            verify(asset(root, part["path"]), part)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".assembling", delete=False) as output:
                temporary = Path(output.name)
                for part in record["parts"]:
                    with asset(root, part["path"]).open("rb") as stream:
                        for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
                            output.write(block)
            verify(temporary, expected)
            temporary.replace(destination)
            print(f"Restored {record['path']}", flush=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    for record in records:
        verify(asset(root, record["path"]), record)
    print(f"Verified all {len(records)} original model files; no network access needed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Restore Git LFS model parts and verify all checkpoint hashes offline.")
    parser.add_argument("--prepare", action="store_true", help="Maintainer only: split verified original weights for Git LFS.")
    args = parser.parse_args()
    try:
        if args.prepare:
            manifest = prepare(ROOT)
            print(f"Prepared {len(manifest['files'])} split model files.")
        else:
            restore(ROOT)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()