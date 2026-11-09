import argparse
import contextlib
import json
import sys
from pathlib import Path

from .inference import Engine, Request


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate offline instrumental music as a 48 kHz stereo WAV.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--bpm", type=int)
    parser.add_argument("--key", default="")
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.85)
    parser.add_argument("--fade-in", type=float, default=0)
    parser.add_argument("--fade-out", type=float, default=0)
    parser.add_argument("--overwrite", action="store_true")
    args = vars(parser.parse_args())
    output = args.pop("output")
    overwrite = args.pop("overwrite")
    try:
        request = Request(**args)
        request.validate()
        if output.suffix.lower() != ".wav":
            raise ValueError("Output must have a .wav extension.")
        if not overwrite and (output.exists() or output.with_suffix(".json").exists()):
            raise FileExistsError(f"Output already exists: {output}")
        with contextlib.redirect_stdout(sys.stderr):
            engine = Engine()
            try:
                result = engine.generate(request, output, overwrite)
            finally:
                engine.close()
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())