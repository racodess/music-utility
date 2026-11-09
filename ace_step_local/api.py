import argparse
import asyncio
import contextlib
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .inference import ROOT, Engine, Request


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=512)
    duration: float = Field(default=60, ge=30, le=300, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=4294967295, strict=True)
    bpm: int | None = Field(default=None, ge=30, le=300, strict=True)
    key: str = Field(default="", max_length=40)
    steps: int = Field(default=8, ge=1, le=20, strict=True)
    temperature: float = Field(default=0.85, ge=0, le=2, allow_inf_nan=False)
    fade_in: float = Field(default=0, ge=0, allow_inf_nan=False)
    fade_out: float = Field(default=0, ge=0, allow_inf_nan=False)


def create_app(engine_factory=Engine, output_dir: Path | None = None) -> FastAPI:
    destination = output_dir or ROOT / "outputs/api"
    jobs: dict[str, dict] = {}
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)

    @asynccontextmanager
    async def lifespan(app):
        def initialize():
            with contextlib.redirect_stdout(sys.stderr):
                return engine_factory()

        engine = await asyncio.to_thread(initialize)
        app.state.ready = True

        async def worker():
            while True:
                job_id, request = await queue.get()
                jobs[job_id]["status"] = "running"
                try:
                    def generate():
                        with contextlib.redirect_stdout(sys.stderr):
                            return engine.generate(request, destination / f"{job_id}.wav")

                    result = await asyncio.to_thread(generate)
                    jobs[job_id].update(status="succeeded", result=result)
                except Exception as error:
                    jobs[job_id].update(status="failed", error=str(error))
                finally:
                    queue.task_done()

        worker_task = asyncio.create_task(worker())
        try:
            yield
        finally:
            app.state.ready = False
            await queue.join()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
            await asyncio.to_thread(engine.close)

    app = FastAPI(title="ACE-Step Local Instrumental API", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.ready = False

    @app.get("/health")
    async def health():
        return {"ready": app.state.ready, "queued": queue.qsize(),
                "running": sum(job["status"] == "running" for job in jobs.values())}

    @app.post("/generate", status_code=202)
    async def submit(body: GenerateRequest):
        request = Request(**body.model_dump())
        try:
            request.validate()
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if not app.state.ready or queue.full():
            raise HTTPException(503, "Generation queue is full or service is not ready.")
        if len(jobs) >= 32:
            completed = next((key for key, value in jobs.items()
                              if value["status"] in {"succeeded", "failed"}), None)
            if completed is None:
                raise HTTPException(503, "Job capacity reached.")
            del jobs[completed]
        job_id = uuid.uuid4().hex
        jobs[job_id] = {"id": job_id, "status": "queued"}
        queue.put_nowait((job_id, request))
        return {"id": job_id, "status": "queued", "status_url": f"/jobs/{job_id}",
                "audio_url": f"/jobs/{job_id}/audio"}

    @app.get("/jobs/{job_id}")
    async def status(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "Unknown or expired job.")
        return jobs[job_id]

    @app.get("/jobs/{job_id}/audio")
    async def audio(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown or expired job.")
        if job["status"] != "succeeded":
            raise HTTPException(409, "Audio is not ready.")
        return FileResponse(destination / f"{job_id}.wav", media_type="audio/wav", filename=f"{job_id}.wav")

    return app


def main():
    parser = argparse.ArgumentParser(description="Run the offline localhost music API.")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()