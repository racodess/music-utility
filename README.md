# ACE-Step 1.5 Offline Music Bundle

Instrumental-only music generation on Apple Silicon, without runtime downloads.
Outputs are 48 kHz stereo PCM24 WAVs with JSON settings/seed sidecars.

## GitHub Repository Contents

This repository contains the application source, scripts, tests, dependency pins,
provenance, and the local upstream patch. Model weights, bundled Python, installed
dependencies, wheel archives, caches, generated audio, and `dist/` are intentionally
excluded from Git because this is not a suitable store for the multi-gigabyte bundle.
A Git clone by itself is not a runnable offline installation. Use the complete
native bundle or the `utility` Docker transport image containing `ace-step.zip`
for offline deployment; the image must first be published to your own registry.

The upstream source is pinned as a Git submodule. To obtain the source for inspection
or rebuilding:

```bash
git clone --recurse-submodules https://github.com/racodess/music-utility.git
cd music-utility
git -C vendor/ACE-Step-1.5 apply ../../offline/upstream.patch
```

Applying the patch is only needed for a fresh source checkout. Do not apply it again
to the already-patched local installation. Runtime and checkpoint preparation are
separate from cloning; the local-use commands below assume those assets are present.

## Local Use

Run from this folder:

```bash
bash generate.sh --prompt "Calm piano and warm strings, documentary background" \
  --duration 60 --seed 42 --output outputs/music.wav
```

Omit `--seed` for a new variation; the chosen seed is recorded in the JSON sidecar.
Durations: 30-300 seconds. Optional controls: `--bpm`, `--key`, `--steps` (default 8),
`--temperature`, `--fade-in`, `--fade-out`. Existing output files require `--overwrite`.
The CLI prints a single JSON result to stdout and progress/errors to stderr.
It exits nonzero on failure and never silently shortens the requested track.
Do not run the CLI and API simultaneously from the same installation.

## Optional Localhost API

```bash
bash serve.sh --port 8001
curl http://127.0.0.1:8001/health
curl -X POST http://127.0.0.1:8001/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Calm piano and strings","duration":60,"seed":42}'
```

Submission returns HTTP 202 with `id`, `status_url`, and `audio_url`.
Poll the returned status URL until `status` is `succeeded` or `failed`, then fetch
the audio URL to obtain the WAV. `result` contains seed, format, timings, and settings.
Set a generous generation deadline in the video utility; generation is asynchronous.

Only one job generates at a time, with at most two waiting jobs. A full queue returns
HTTP 503. Health remains responsive. Only the 32 most recent job records are retained;
job records disappear on restart, but WAV/JSON outputs remain in `outputs/api/`.
Delete unneeded output files manually. Shutdown drains accepted jobs before releasing
models. The service binds only to 127.0.0.1 and does not expose arbitrary file paths,
model switching, or lyrics. API documentation is available in `/openapi.json`;
browser documentation is disabled to avoid external CDN dependencies.

## Transfer Through OneDrive

The `dist/` directory is the transfer payload, not the original model cache.
Transfer **all** its contents together, including the small application directory,
setup scripts, manifests, checksums, and every numbered archive part.
Each part is at most 500,000,000 bytes, safely below the 1000 MB transfer limit.
Parts such as `models.tar.gz.part001` are pieces of one gzip archive, not independent
tarballs. Do not unpack them individually or rename/reorder them.

On the work Mac, fully download the payload and place it in a normal local folder,
preferably outside active OneDrive synchronization. Open Terminal in that folder:

```bash
bash setup.sh
bash generate.sh --prompt "Calm documentary background music, piano and strings" \
  --duration 30 --output outputs/work-mac-test.wav
```

Setup checks every part, automatically reassembles/unpacks the archives, verifies
runtime/model/dependency files, and creates its Python environment from local,
hash-pinned wheels. No Python installation, Homebrew, Git, uv, compiler, administrator
access, or internet dependency download is required. Setup can be rerun and preserves
outputs. After moving an unpacked installation to another path, rerun setup to recreate
the virtual environment; do not assume copied virtual environments are relocatable.

Requirements: Apple Silicon ARM64, macOS 26.0 or newer, sufficient RAM and disk space.
Model files occupy about 10 GB; peak extraction/install space is larger because archive
parts and unpacked files coexist. This installation was tested on an M1 Pro with 16 GB;
the target M4 Pro/24 GB Mac still needs its own offline smoke test.

If Gatekeeper or organizational controls block the bundled executables, use your
organization's approved process. Setup does not remove quarantine, disable protections,
or request privileged access. Missing/corrupt files fail with an error, not an online repair.

## Validation and Performance

Real generation passed at 30, 120, 240, and 300 seconds. Loaded-model generation took
approximately 39, 127, 221, and 271 seconds respectively on the M1 Pro for the test
prompt/settings. Startup adds roughly 40 seconds and may vary with memory pressure.
The API avoids repeated model loading. M4 Pro performance has not been measured.
The same seed produced byte-identical 30-second WAVs in the local test; a different
seed produced a different result. Exact reproducibility across chips/OS versions is
not guaranteed. Listening approval remains outstanding: instrumental conditioning
does not guarantee that a generative model can never emit vocal-like sounds.

The release passed fresh extraction and two offline setup runs in a folder containing
spaces, with an empty HOME, only macOS system tools on PATH, and the original runtime,
environment, and checkpoints temporarily hidden. macOS sandboxing denied external
networking throughout setup and a real 300-second generation. That WAV was byte-identical
to the same-seed local result. Nine focused tests passed. The compressed payload contains
18 archive parts totaling about 8.38 GB, each at most 500 MB.

Audition `outputs/test-30-mps.wav` and `outputs/test-300.wav` in this local installation.
The isolated-test report is `outputs/release-verification.json`; its log and WAV are
retained as `outputs/release-verification.log` and `outputs/offline-300.wav`.
Test outputs/logs are intentionally excluded from the transfer payload.

## Provenance and Rebuilding

Upstream: https://github.com/ace-step/ACE-Step-1.5
Source commit: `ca1e85fe9430179831e6bc6be790c332190a3866`.
Model: https://huggingface.co/ACE-Step/Ace-Step1.5
Model revision: `19671f406d603126926c1b7e2adc169acbcade22`.
Standalone CPython: 3.12.16 ARM64. Exact package versions/hashes are in
`offline/requirements.lock`; platform/build metadata is in `offline/build.json`.

The checked-out upstream source remains in `vendor/ACE-Step-1.5/` here. The release
contains its Python source in the installed/bundled upstream wheel, plus the local
patch in `offline/upstream.patch`. The patch selects float16 for MPS DiT/VAE loading
instead of float32. DiT uses PyTorch MPS to avoid duplicate MLX DiT weights; the LM
and VAE use MLX. No model variant was changed and memory safety limits remain enabled.

The initial cache had broken external blob links. `scripts/prepare_models.py` fetched
the same revision into `checkpoints/` and verified weights against HF metadata; the
original cache was not changed. Upstream may synchronize model-side Python code during
initialization; the final bundle manifest records the actual staged files.

For maintainers with internet access here only: `scripts/prepare.sh` builds/retains
dependency wheels. After changes, rerun tests and real generation, then run:

```bash
.venv/bin/python scripts/finalize.py
.venv/bin/python scripts/package_release.py --output dist
.venv/bin/python scripts/verify_release.py
```

Packaging requires an empty destination and leaves the unpacked local installation
usable. Verification requires a stopped API, at least 14 GiB free, and a fresh
`.verification/Offline Bundle` destination; it restores the original installation
on exit. Do not interrupt it forcibly while the original directories are hidden.
Do not publish unvalidated rebuilds or discard the pinned wheelhouse.