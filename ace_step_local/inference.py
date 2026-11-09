import fcntl
import ipaddress
import json
import math
import os
import random
import secrets
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "acestep-v15-turbo": ("config.json", "model.safetensors", "silence_latent.pt"),
    "vae": ("config.json", "diffusion_pytorch_model.safetensors"),
    "Qwen3-Embedding-0.6B": ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"),
    "acestep-5Hz-lm-1.7B": ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"),
}


def configure_offline(root: Path = ROOT) -> None:
    settings = {
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "GRADIO_ANALYTICS_ENABLED": "False",
        "TOKENIZERS_PARALLELISM": "false", "ACESTEP_SAVE_MEMORY": "1",
        "ACESTEP_PROJECT_ROOT": str(root), "ACESTEP_CHECKPOINTS_DIR": str(root / "checkpoints"),
        "HF_HOME": str(root / ".cache/huggingface"), "MPLCONFIGDIR": str(root / ".cache/matplotlib"),
        "XDG_CACHE_HOME": str(root / ".cache"), "NUMBA_CACHE_DIR": str(root / ".cache/numba"),
        "TORCH_HOME": str(root / ".cache/torch"),
    }
    os.environ.update(settings)

    def network_guard(event, arguments):
        host = None
        if event == "socket.getaddrinfo":
            host = arguments[0]
        elif event in {"socket.connect", "socket.sendto"}:
            address = arguments[1]
            if isinstance(address, tuple):
                host = address[0]
        if host is None or host in {"localhost", b"localhost"}:
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        raise PermissionError(f"Offline mode blocked network access to {host!r}")

    sys.addaudithook(network_guard)


def preflight(root: Path = ROOT) -> None:
    missing = [f"{component}/{name}" for component, names in COMPONENTS.items()
               for name in names if not (root / "checkpoints" / component / name).is_file()]
    if missing:
        raise RuntimeError("Missing local model files (no download attempted): " + ", ".join(missing))


@dataclass
class Request:
    prompt: str
    duration: float = 60
    seed: int | None = None
    bpm: int | None = None
    key: str = ""
    steps: int = 8
    temperature: float = 0.85
    fade_in: float = 0
    fade_out: float = 0

    def validate(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 512:
            raise ValueError("Prompt must contain 1-512 characters.")
        if not math.isfinite(self.duration) or not 30 <= self.duration <= 300:
            raise ValueError("Duration must be 30-300 seconds.")
        if self.seed is not None and (type(self.seed) is not int or not 0 <= self.seed <= 2**32 - 1):
            raise ValueError("Seed must be an integer between 0 and 4294967295.")
        if self.bpm is not None and (type(self.bpm) is not int or not 30 <= self.bpm <= 300):
            raise ValueError("BPM must be between 30 and 300.")
        if type(self.steps) is not int or not 1 <= self.steps <= 20:
            raise ValueError("Turbo steps must be between 1 and 20.")
        if not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("Temperature must be between 0 and 2.")
        if any(not math.isfinite(value) or not 0 <= value <= self.duration for value in (self.fade_in, self.fade_out)):
            raise ValueError("Fades must be between zero and the track duration.")


class Engine:
    def __init__(self, root: Path = ROOT):
        preflight(root)
        configure_offline(root)
        self.root = root
        self.lock = threading.Lock()
        (root / ".cache").mkdir(exist_ok=True)
        self.process_lock = (root / ".cache/generation.lock").open("a")
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.process_lock.close()
            raise RuntimeError("Another CLI or API process is using this bundle.") from None
        try:
            import torch
            from acestep.handler import AceStepHandler
            from acestep.llm_inference import LLMHandler

            if not torch.backends.mps.is_available():
                raise RuntimeError("Apple Silicon MPS is unavailable; this bundle requires macOS ARM64.")
            self.dit = AceStepHandler()
            status, success = self.dit.initialize_service(str(root), "acestep-v15-turbo", device="mps", vae_checkpoint="official", use_mlx_dit=False)
            if not success:
                raise RuntimeError(status)
            self.lm = LLMHandler()
            status, success = self.lm.initialize(str(root / "checkpoints"), "acestep-5Hz-lm-1.7B", backend="mlx", device="mps")
            if not success or self.lm.llm_backend != "mlx":
                raise RuntimeError("MLX LM initialization failed: " + status)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.process_lock.close()

    def generate(self, request: Request, output: Path, overwrite: bool = False) -> dict:
        request.validate()
        output = output.expanduser().resolve()
        if output.suffix.lower() != ".wav":
            raise ValueError("Output must have a .wav extension.")
        sidecar = output.with_suffix(".json")
        if not overwrite and (output.exists() or sidecar.exists()):
            raise FileExistsError(f"Output already exists: {output}")
        from acestep.gpu_config import check_duration_limit, get_global_gpu_config
        valid, message = check_duration_limit(request.duration, get_global_gpu_config(), True)
        if not valid:
            raise ValueError(message + " This bundle refuses to silently shorten a track.")
        with self.lock:
            return self._generate(request, output, sidecar)

    def _generate(self, request: Request, output: Path, sidecar: Path) -> dict:
        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        import torch
        from acestep.inference import GenerationConfig, GenerationParams, generate_music

        seed = request.seed if request.seed is not None else secrets.randbits(32)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.mps.manual_seed(seed)
        mx.random.seed(seed)
        params = GenerationParams(
            caption=request.prompt.strip() + ", instrumental music, no vocals",
            lyrics="[Instrumental]", instrumental=True, duration=request.duration,
            seed=seed, bpm=request.bpm, keyscale=request.key, vocal_language="unknown",
            inference_steps=request.steps, thinking=True, lm_temperature=request.temperature,
            use_cot_caption=False, use_cot_language=False,
            fade_in_duration=request.fade_in, fade_out_duration=request.fade_out,
        )
        config = GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format="wav")
        started = time.monotonic()
        result = generate_music(self.dit, self.lm, params, config, save_dir=None)
        if not result.success or len(result.audios) != 1:
            raise RuntimeError(result.error or result.status_message or "Generation returned no audio.")
        audio = result.audios[0]
        samples = audio["tensor"].detach().cpu().float().numpy().T
        rate = audio["sample_rate"]
        duration = len(samples) / rate
        if rate != 48000 or samples.ndim != 2 or samples.shape[1] != 2:
            raise RuntimeError("Unexpected audio format: expected 48 kHz stereo.")
        if abs(duration - request.duration) > 0.25:
            raise RuntimeError(f"Generated duration {duration:.3f}s differs from requested {request.duration}s.")
        if not np.isfinite(samples).all() or np.max(np.abs(samples)) < 1e-6:
            raise RuntimeError("Generated audio is silent or contains non-finite samples.")
        build = json.loads((self.root / "offline/build.json").read_text())
        metadata = {
            "output": str(output), "seed": seed, "instrumental": True,
            "request": asdict(request), "duration": duration, "sample_rate": rate,
            "channels": 2, "subtype": "PCM_24", "peak": float(np.max(np.abs(samples))),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "backend": {"lm": self.lm.llm_backend, "dit": "mlx" if self.dit.use_mlx_dit else "mps",
                        "vae": "mlx" if self.dit.use_mlx_vae else "mps"},
            "build": build, "timings": result.extra_outputs.get("time_costs", {}),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".ace-step-", dir=output.parent) as staging:
            staged_wav = Path(staging) / "audio.wav"
            staged_json = Path(staging) / "audio.json"
            sf.write(staged_wav, samples, rate, format="WAV", subtype="PCM_24")
            staged_json.write_text(json.dumps(metadata, indent=2) + "\n")
            os.replace(staged_json, sidecar)
            os.replace(staged_wav, output)
        mx.clear_cache()
        torch.mps.empty_cache()
        return metadata