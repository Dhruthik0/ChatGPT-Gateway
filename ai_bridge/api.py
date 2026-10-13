import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config as cfg_mod
from . import prompt as prompt_mod
from .adapter import ChatGPTAdapter
from .browser import BrowserManager
from .errors import BridgeError
from .jobs import JobManager

log = logging.getLogger("ai_bridge.api")

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


class AskRequest(BaseModel):
    instruction: str = Field(min_length=1)
    input: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=900)
    new_chat: bool = True


def create_app(adapter=None, config=None) -> FastAPI:
    cfg = config or cfg_mod.load()
    browser = None
    if adapter is None:
        browser = BrowserManager(cfg.cdp_url)
        adapter = ChatGPTAdapter(browser)
    jobs = JobManager(adapter, max_queue=cfg.max_queue)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if browser:
            await browser.start()
        jobs.start()
        log.info("service.start host=%s port=%d", cfg.host, cfg.port)
        yield
        await jobs.stop()
        if browser:
            await browser.close()

    app = FastAPI(title="Local AI Bridge", lifespan=lifespan)  # no CORS, by design

    @app.exception_handler(BridgeError)
    async def _bridge_error(_: Request, exc: BridgeError):
        return JSONResponse(status_code=exc.status, content=exc.payload())

    async def require_auth(request: Request) -> None:
        host = request.headers.get("host", "").rsplit(":", 1)[0]
        if host not in _LOOPBACK:            # blocks DNS rebinding from a web page
            raise BridgeError("FORBIDDEN_HOST")
        header = request.headers.get("authorization", "")
        token = header[7:] if header[:7].lower() == "bearer " else ""
        if not secrets.compare_digest(token, cfg.token):
            raise BridgeError("UNAUTHORIZED", hint=f"The token lives in {cfg_mod.CONFIG_FILE}")

    # ---------- routes ----------

    @app.post("/ask", dependencies=[Depends(require_auth)])
    async def ask(req: AskRequest, request: Request):
        run_async = request.query_params.get("async") in ("1", "true", "yes")
        size = len(req.instruction.encode()) + len((req.input or "").encode())
        if size > cfg.max_input_bytes:
            raise BridgeError("INPUT_TOO_LARGE", f"{size} bytes > {cfg.max_input_bytes}")

        text = prompt_mod.build(req.instruction, req.input)
        job = jobs.submit(text, req.timeout or cfg.default_timeout, req.new_chat)
        log.info("job.received id=%s instruction_len=%d input_len=%d async=%s",
                 job.id, len(req.instruction), len(req.input or ""), run_async)
        if cfg.log_bodies:
            log.debug("job.prompt id=%s body=%r", job.id, text)

        if run_async:
            return JSONResponse(status_code=202, content={"success": True, "job_id": job.id, "status": "queued"})

        await job.done.wait()
        if job.error:
            raise job.error
        return {"success": True, "response": job.result, "job_id": job.id,
                "duration_ms": job.duration_ms, "source": job.source}

    @app.get("/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def get_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise BridgeError("JOB_NOT_FOUND")
        return job.public()

    @app.delete("/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def cancel_job(job_id: str):
        job = jobs.get(job_id)
        if not job:
            raise BridgeError("JOB_NOT_FOUND")
        await jobs.cancel(job)
        return {"success": True, "job_id": job.id, "status": job.status}

    @app.get("/health")
    async def health():
        try:
            report = await adapter.health()
        except BridgeError as exc:
            report = {"error": exc.code}
        ok = report.get("authenticated") and not report.get("challenge")
        return {"status": "ok" if ok else "degraded", "queue_depth": jobs.queue.qsize(), **report}

    @app.get("/health/dom", dependencies=[Depends(require_auth)])
    async def health_dom():
        probe = await adapter.dom_probe()
        return {"status": "ok" if all(probe.values()) else "drift", "selectors": probe}

    return app
