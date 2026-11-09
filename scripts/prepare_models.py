import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["HF_HOME"] = str(ROOT / ".cache" / "huggingface")

from huggingface_hub import HfApi, snapshot_download

REVISION = "19671f406d603126926c1b7e2adc169acbcade22"
REPOSITORY = "ACE-Step/Ace-Step1.5"


def main():
    info = HfApi().model_info(REPOSITORY, revision=REVISION, files_metadata=True)
    checkpoint_dir = ROOT / "checkpoints"
    snapshot_download(REPOSITORY, revision=REVISION, local_dir=checkpoint_dir)
    records = []
    for entry in info.siblings:
        path = checkpoint_dir / entry.rfilename
        if not path.is_file():
            raise RuntimeError(f"Missing model file: {entry.rfilename}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(block)
        checksum = digest.hexdigest()
        if entry.lfs is not None and checksum != entry.lfs.sha256:
            raise RuntimeError(f"Model hash mismatch: {entry.rfilename}")
        records.append({"path": entry.rfilename, "size": path.stat().st_size, "sha256": checksum})
    destination = ROOT / "offline" / "models.json"
    destination.write_text(json.dumps({"repository": REPOSITORY, "revision": REVISION, "files": records}, indent=2) + "\n")
    print(f"Verified {len(records)} model files at {REVISION}")


if __name__ == "__main__":
    main()