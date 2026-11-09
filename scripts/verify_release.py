import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))'


def main():
    parser = argparse.ArgumentParser(description="Cold-test a release with original runtime/models hidden and external networking denied.")
    parser.add_argument("--release", type=Path, default=ROOT / "dist")
    parser.add_argument("--destination", type=Path, default=ROOT / ".verification/Offline Bundle")
    args = parser.parse_args()
    source = args.release.resolve()
    destination = args.destination.resolve()
    if destination.exists():
        raise SystemExit("Verification destination must not already exist.")
    if shutil.which("sandbox-exec") is None:
        raise SystemExit("macOS sandbox-exec is required for this developer verification.")
    if shutil.disk_usage(ROOT).free < 14 * 1024**3:
        raise SystemExit("At least 14 GiB free is required for independent extraction and verification.")
    transfer = json.loads((source / "transfer.json").read_text())
    for entry in transfer["parts"]:
        if not 0 < entry["size"] <= 500_000_000:
            raise RuntimeError(f"Invalid part size: {entry['name']}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    def copy_payload(source_path, destination_path):
        if ".tar.gz.part" in Path(source_path).name:
            os.link(source_path, destination_path)
            return destination_path
        return shutil.copy2(source_path, destination_path)

    shutil.copytree(source, destination, copy_function=copy_payload)
    home = destination / "empty-home"
    temporary = destination / "temporary"
    home.mkdir()
    temporary.mkdir()
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
                   "TMPDIR": str(temporary), "LANG": "en_US.UTF-8"}
    hidden = []
    try:
        for name in (".venv", "runtime", "checkpoints"):
            original = ROOT / name
            moved = ROOT / f".verification-hidden-{name.lstrip('.')}"
            if moved.exists():
                raise RuntimeError(f"Existing hidden directory: {moved}")
            original.rename(moved)
            hidden.append((original, moved))
        with (destination / "verification.log").open("w") as log:
            for command in (["/bin/bash", "setup.sh"], ["/bin/bash", "setup.sh"],
                            ["/bin/bash", "generate.sh", "--prompt",
                             "Gentle cinematic piano and warm strings, calm documentary background, restrained percussion, evolving melody",
                             "--duration", "300", "--seed", "42", "--output", "outputs/offline-300.wav"]):
                print("Verifying:", " ".join(command[:3]), flush=True)
                subprocess.run(["/usr/bin/sandbox-exec", "-p", PROFILE, *command], cwd=destination,
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        metadata = json.loads((destination / "outputs/offline-300.json").read_text())
        if metadata["duration"] != 300 or metadata["sample_rate"] != 48000 or metadata["channels"] != 2:
            raise RuntimeError("Invalid cold-start WAV metadata.")
        expected = ROOT / "outputs/test-300.wav"
        actual = destination / "outputs/offline-300.wav"
        identical = hashlib.sha256(expected.read_bytes()).digest() == hashlib.sha256(actual.read_bytes()).digest()
        report = {"passed": True, "external_network_denied": True, "empty_home": True,
                  "original_runtime_models_hidden": True, "destination_contains_spaces": " " in str(destination),
                  "second_setup_passed": True, "duration": metadata["duration"],
                  "same_seed_identical_to_local": identical, "parts": len(transfer["parts"]),
                  "maximum_part_bytes": max(entry["size"] for entry in transfer["parts"]),
                  "destination": str(destination)}
        (ROOT / "outputs/release-verification.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
    finally:
        for original, moved in reversed(hidden):
            moved.rename(original)


if __name__ == "__main__":
    main()