import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ace_step_local.api import create_app
from ace_step_local.inference import Request, preflight
from scripts import finalize, package_release


class InputTests(unittest.TestCase):
    def test_durations(self):
        for duration in (float("nan"), float("inf"), 29, 301):
            with self.assertRaises(ValueError):
                Request("piano", duration).validate()
        Request("piano", 300, 42).validate()

    def test_integer_settings(self):
        for seed in (True, 42.5, -1, 2**32):
            with self.assertRaises(ValueError):
                Request("piano", seed=seed).validate()

    def test_missing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "no download attempted"):
                preflight(Path(directory))

    def test_offline_network_guard(self):
        code = "from ace_step_local.inference import configure_offline; import socket; configure_offline(); socket.getaddrinfo('example.com', 443)"
        result = subprocess.run([__import__("sys").executable, "-c", code], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Offline mode blocked", result.stderr)


class ApiTests(unittest.TestCase):
    def test_failed_generation_is_reported(self):
        class FailingEngine:
            def generate(self, request, output):
                raise RuntimeError("Controlled generation failure")

            def close(self):
                pass

        client = TestClient(create_app(FailingEngine))
        with client:
            submitted = client.post("/generate", json={"prompt": "piano"}).json()
        result = client.get(submitted["status_url"]).json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "Controlled generation failure")
        self.assertEqual(client.get(submitted["audio_url"]).status_code, 409)

    def test_jobs_queue_validation_and_download(self):
        started = threading.Event()
        release = threading.Event()

        class FakeEngine:
            def generate(self, request, output):
                started.set()
                if not release.wait(10):
                    raise RuntimeError("Test timeout")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"RIFF-test-audio")
                return {"seed": request.seed, "output": str(output)}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(FakeEngine, Path(directory))) as client:
                try:
                    self.assertTrue(client.get("/health").json()["ready"])
                    self.assertEqual(client.post("/generate", json={"prompt": "piano", "lyrics": "hello"}).status_code, 422)
                    self.assertEqual(client.post("/generate", json={"prompt": "piano", "duration": 301}).status_code, 422)
                    self.assertEqual(client.get("/jobs/unknown").status_code, 404)
                    first = client.post("/generate", json={"prompt": "piano", "seed": 42}).json()
                    self.assertTrue(started.wait(10))
                    self.assertEqual(client.get(first["status_url"]).json()["status"], "running")
                    self.assertEqual(client.get(first["audio_url"]).status_code, 409)
                    self.assertEqual(client.get("/health").json()["running"], 1)
                    for _ in range(2):
                        self.assertEqual(client.post("/generate", json={"prompt": "piano"}).status_code, 202)
                    self.assertEqual(client.post("/generate", json={"prompt": "piano"}).status_code, 503)
                finally:
                    release.set()
                for _ in range(100):
                    if client.get(first["status_url"]).json()["status"] == "succeeded":
                        break
                self.assertEqual(client.get(first["audio_url"]).content, b"RIFF-test-audio")


class PackagingTests(unittest.TestCase):
    def test_native_copy_relocation(self):
        import shutil
        from ace_step_local.inference import ROOT
        for mode in ("broken-link", "absent-link", "directory"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix="ace native copy ") as directory:
                target = Path(directory)
                (target / "scripts").mkdir()
                shutil.copy2(ROOT / "scripts/setup_native_copy.sh", target / "scripts/setup_native_copy.sh")
                binary = target / "runtime/cpython-3.12.16-macos-aarch64-none/bin/python3.12"
                binary.parent.mkdir(parents=True)
                binary.touch(mode=0o755)
                alias = target / "runtime/cpython-3.12-macos-aarch64-none"
                if mode == "broken-link":
                    alias.symlink_to("/nonexistent/old-mac/python")
                elif mode == "directory":
                    alias.mkdir()
                stamp = target / ".venv/.bundle-installed"
                stamp.parent.mkdir()
                stamp.write_text("old-path")
                (target / "setup.sh").write_text("test ! -f .venv/.bundle-installed\n")
                result = subprocess.run(["/bin/bash", "scripts/setup_native_copy.sh"], cwd=target, capture_output=True, text=True)
                if mode == "directory":
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(alias.is_dir())
                    self.assertTrue(stamp.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(alias.readlink(), Path("cpython-3.12.16-macos-aarch64-none"))
                    self.assertFalse(stamp.exists())

    def test_manifest_excludes_generated_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "offline/wheels").mkdir(parents=True)
            (root / "offline/build.json").write_text("{}")
            for name in ("runtime/library.py", "runtime/__pycache__/library.pyc", "checkpoints/.cache/download"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            with patch.object(finalize, "ROOT", root), patch.object(finalize.importlib.metadata, "distributions", return_value=[]), patch.object(finalize.subprocess, "check_output", return_value=""):
                finalize.main()
            manifest = json.loads((root / "bundle-manifest.json").read_text())
            names = [entry["path"] for entry in manifest["files"]]
            self.assertIn("runtime/library.py", names)
            self.assertFalse(any("__pycache__" in name or ".cache" in name for name in names))

    def test_numbered_parts_and_hashes(self):
        original_size = package_release.PART_SIZE
        package_release.PART_SIZE = 11
        try:
            with tempfile.TemporaryDirectory() as directory:
                writer = package_release.PartWriter(Path(directory), "test.tar.gz")
                data = b"abcdefghijklmnopqrstuvwxyz"
                writer.write(data[:7])
                writer.write(data[7:])
                writer.close_part()
                paths = sorted(Path(directory).glob("*.part*"))
                self.assertEqual(b"".join(path.read_bytes() for path in paths), data)
                self.assertEqual([path.stat().st_size for path in paths], [11, 11, 4])
                self.assertEqual(writer.archive_digest.hexdigest(), hashlib.sha256(data).hexdigest())
                for entry in writer.parts:
                    self.assertEqual(entry["sha256"], hashlib.sha256((Path(directory) / entry["name"]).read_bytes()).hexdigest())
        finally:
            package_release.PART_SIZE = original_size

    def test_corrupt_part_fails_before_python(self):
        import shutil
        from ace_step_local.inference import ROOT
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copy2(ROOT / "setup.sh", target / "setup.sh")
            (target / "broken.part001").write_bytes(b"corrupt")
            (target / "transfer.sha256").write_text("0" * 64 + "  broken.part001\n")
            result = subprocess.run(["/bin/bash", str(target / "setup.sh")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAILED", result.stdout)


if __name__ == "__main__":
    unittest.main()