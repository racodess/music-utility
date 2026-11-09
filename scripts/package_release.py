import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PART_SIZE = 500_000_000


class PartWriter:
    def __init__(self, directory, name):
        self.directory = directory
        self.name = name
        self.count = 0
        self.stream = None
        self.size = 0
        self.digest = None
        self.parts = []
        self.archive_digest = hashlib.sha256()

    def write(self, data):
        count = len(data)
        self.archive_digest.update(data)
        while data:
            if self.stream is None:
                self.count += 1
                self.filename = f"{self.name}.part{self.count:03d}"
                self.stream = (self.directory / self.filename).open("wb")
                self.size = 0
                self.digest = hashlib.sha256()
            section = data[:PART_SIZE - self.size]
            self.stream.write(section)
            self.digest.update(section)
            self.size += len(section)
            data = data[len(section):]
            if self.size == PART_SIZE:
                self.close_part()
        return count

    def flush(self):
        if self.stream:
            self.stream.flush()

    def close_part(self):
        if self.stream:
            self.stream.close()
            self.parts.append({"name": self.filename, "size": self.size, "sha256": self.digest.hexdigest()})
            self.stream = None


def exclude_generated(member):
    if ".cache" in Path(member.name).parts or "__pycache__" in Path(member.name).parts:
        return None
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    if member.issym() and os.path.isabs(member.linkname):
        target = Path(member.linkname).relative_to(ROOT)
        member.linkname = os.path.relpath(target, Path(member.name).parent)
    return member


def main():
    parser = argparse.ArgumentParser(description="Package a validated offline music bundle into <=500 MB parts.")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise SystemExit("Release destination must be empty; choose a new output directory.")
    archives = []
    archive_records = []
    parts = []
    for directory in ("runtime", "offline", "checkpoints"):
        name = {"runtime": "runtime.tar.gz", "offline": "dependencies.tar.gz", "checkpoints": "models.tar.gz"}[directory]
        writer = PartWriter(destination, name)
        with tarfile.open(fileobj=writer, mode="w|gz", compresslevel=6, dereference=False) as archive:
            archive.add(ROOT / directory, arcname=directory, filter=exclude_generated)
        writer.close_part()
        archives.append(name)
        archive_records.append({"name": name, "sha256": writer.archive_digest.hexdigest(),
                    "parts": [entry["name"] for entry in writer.parts]})
        parts.extend(writer.parts)
        print(f"Packed {directory}: {len(writer.parts)} parts", flush=True)
    for name in ("setup.sh", "generate.sh", "serve.sh", "README.md", "THIRD_PARTY_NOTICES.md", "bundle-manifest.json"):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copytree(ROOT / "ace_step_local", destination / "ace_step_local", ignore=shutil.ignore_patterns("__pycache__"))
    (destination / "archives.txt").write_text("\n".join(archives) + "\n")
    (destination / "transfer.json").write_text(json.dumps({"maximum_part_bytes": PART_SIZE, "archives": archive_records, "parts": parts}, indent=2) + "\n")
    application_files = [path for path in destination.rglob("*") if path.is_file() and ".part" not in path.name]
    checksums = [f"{entry['sha256']}  {entry['name']}" for entry in parts]
    for path in sorted(application_files):
        checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(destination)}")
    (destination / "transfer.sha256").write_text("\n".join(checksums) + "\n")
    print(f"Release ready: {destination}")


if __name__ == "__main__":
    main()