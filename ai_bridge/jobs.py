import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field

from .errors import BridgeError

log = logging.getLogger("ai_bridge.jobs")

CANCEL_GRACE = 10       # s to let a running job unwind before DELETE answers anyway


@dataclass
class Job:
    id: str
    prompt: str
    timeout: int
    new_chat: bool
    status: str = "queued"          # queued | running | done | failed | cancelled
    result: str | None = None
    source: str | None = None       # "stream" or "dom" -- which extractor answered
    error: BridgeError | None = None
    created: float = field(default_factory=time.monotonic)
    finished: float | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = None

    @property
    def duration_ms(self) -> int:
        return int(((self.finished or time.monotonic()) - self.created) * 1000)

    def public(self) -> dict:
        out = {"job_id": self.id, "status": self.status, "duration_ms": self.duration_ms}
        if self.status == "done":
            out |= {"success": True, "response": self.result, "source": self.source}
        elif self.error:
            out |= self.error.payload()
        return out


class JobManager:
    """One worker, one tab, one request at a time.

    ponytail: single worker by design -- parallel tabs multiply bot-detection
    risk for near-zero gain at personal scale. Raise the count only if that
    ever stops being true.
    """

    def __init__(self, adapter, max_queue: int = 10, ttl: float = 600):
        self.adapter = adapter
        self.max_queue = max_queue
        self.ttl = ttl
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.jobs: dict[str, Job] = {}
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="ai-bridge-worker")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    def submit(self, prompt: str, timeout: int, new_chat: bool) -> Job:
        self._evict()
        if self.queue.qsize() >= self.max_queue:
            raise BridgeError("BUSY", f"{self.queue.qsize()} requests already queued.")
        job = Job(id="j_" + secrets.token_hex(8), prompt=prompt, timeout=timeout, new_chat=new_chat)
        self.jobs[job.id] = job
        self.queue.put_nowait(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def cancel(self, job: Job) -> None:
        """Returns once the job has actually left `running`, so DELETE can answer
        with the real outcome instead of the status the caller already knew."""
        if job.status in ("done", "failed", "cancelled"):
            return
        if job.status == "running":
            await self.adapter.stop()
            if job._task:
                job._task.cancel()
            # the worker owns the transition; it sets `done` in its finally
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(job.done.wait(), timeout=CANCEL_GRACE)
            return
        job.status = "cancelled"
        job.error = BridgeError("REQUEST_CANCELLED")
        job.finished = time.monotonic()
        job.done.set()

    def _evict(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for jid in [j.id for j in self.jobs.values() if j.finished and j.finished < cutoff]:
            del self.jobs[jid]

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            if job.status == "cancelled":
                continue
            job.status = "running"
            task = asyncio.create_task(self.adapter.ask(job.prompt, job.timeout, job.new_chat))
            job._task = task
            try:
                job.result = await task
                job.source = getattr(self.adapter, "last_source", None)
                job.status = "done"
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise           # the worker itself is shutting down
                job.status = "cancelled"
                job.error = BridgeError("REQUEST_CANCELLED")
            except BridgeError as exc:
                job.status, job.error = "failed", exc
            except Exception as exc:  # noqa: BLE001 - nothing untyped reaches a client
                log.exception("job.crashed id=%s", job.id)
                job.status, job.error = "failed", BridgeError("INTERNAL", str(exc))
            finally:
                job.finished = time.monotonic()
                job.done.set()
                log.info("job.%s id=%s total_ms=%d", job.status, job.id, job.duration_ms)
