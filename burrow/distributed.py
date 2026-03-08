"""Burrow distributed computing — Ray, Dask, and built-in queue wrappers.

All runtimes are optional. Import errors are caught gracefully so peers
without Ray or Dask can still use the built-in queue.
"""

import asyncio
import importlib
import json
import logging
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("burrow.distributed")


# ---------------------------------------------------------------------------
# Job status enum
# ---------------------------------------------------------------------------

class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobInfo:
    job_id: str
    runtime: str
    func: str
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    status: str = JobState.PENDING
    result: Any = None
    error: str | None = None
    progress: float | None = None
    submitted_by: str = ""
    submitted_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    retries: int = 0
    max_retries: int = 0
    tags: list[str] = field(default_factory=list)
    batch_id: str | None = None
    log_lines: list[str] = field(default_factory=list)
    _ray_ref: Any = None
    _dask_future: Any = None

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id,
            "runtime": self.runtime,
            "func": self.func,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "progress": self.progress,
            "submitted_by": self.submitted_by,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retries": self.retries,
            "tags": self.tags,
        }
        if self.batch_id:
            d["batch_id"] = self.batch_id
        if self.completed_at and self.started_at:
            d["duration_s"] = round(self.completed_at - self.started_at, 3)
        return d

    def add_log(self, message: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        self.log_lines.append(line)
        log.debug("job %s: %s", self.job_id, message)


# ---------------------------------------------------------------------------
# Ray wrapper
# ---------------------------------------------------------------------------

class RayRuntime:
    """Wraps Ray for job submission and monitoring."""

    def __init__(self):
        self._ray = None
        self._connected = False

    @property
    def available(self) -> bool:
        try:
            self._ray = importlib.import_module("ray")
            return True
        except ImportError:
            return False

    def connect(self, address: str | None = None) -> bool:
        if not self.available:
            return False
        try:
            if address:
                self._ray.init(address=address, ignore_reinit_error=True)
            else:
                self._ray.init(ignore_reinit_error=True)
            self._connected = True
            log.info("Ray connected (address=%s)", address or "local")
            return True
        except Exception as e:
            log.error("Ray connection failed: %s", e)
            return False

    def submit(self, job: JobInfo) -> bool:
        if not self._connected:
            return False
        try:
            mod_name, func_name = job.func.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            remote_func = self._ray.remote(func)
            ref = remote_func.remote(*job.args, **job.kwargs)
            job._ray_ref = ref
            job.status = JobState.RUNNING
            job.started_at = time.time()
            job.add_log(f"submitted to Ray: {job.func}")
            return True
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
            job.add_log(f"Ray submit failed: {e}")
            return False

    def check_status(self, job: JobInfo) -> str:
        if not self._connected or not job._ray_ref:
            return job.status
        try:
            ready, _ = self._ray.wait([job._ray_ref], timeout=0)
            if ready:
                job.result = self._ray.get(job._ray_ref)
                job.status = JobState.COMPLETED
                job.completed_at = time.time()
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
            job.completed_at = time.time()
        return job.status

    def get_result(self, job: JobInfo, timeout: float = 30.0):
        if not self._connected or not job._ray_ref:
            return None
        try:
            result = self._ray.get(job._ray_ref, timeout=timeout)
            job.result = result
            job.status = JobState.COMPLETED
            job.completed_at = time.time()
            return result
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
            return None

    def cancel(self, job: JobInfo) -> bool:
        if not self._connected or not job._ray_ref:
            return False
        try:
            self._ray.cancel(job._ray_ref)
            job.status = JobState.CANCELLED
            return True
        except Exception:
            return False

    def cluster_info(self) -> dict:
        if not self._connected:
            return {}
        try:
            nodes = self._ray.nodes()
            return {
                "nodes": len(nodes),
                "resources": self._ray.cluster_resources(),
                "available_resources": self._ray.available_resources(),
            }
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Dask wrapper
# ---------------------------------------------------------------------------

class DaskRuntime:
    """Wraps Dask distributed for job submission."""

    def __init__(self):
        self._client = None
        self._connected = False

    @property
    def available(self) -> bool:
        try:
            importlib.import_module("dask.distributed")
            return True
        except ImportError:
            return False

    def connect(self, scheduler_address: str | None = None) -> bool:
        if not self.available:
            return False
        try:
            from dask.distributed import Client
            if scheduler_address:
                self._client = Client(scheduler_address)
            else:
                self._client = Client()
            self._connected = True
            log.info("Dask connected (scheduler=%s)", scheduler_address or "local")
            return True
        except Exception as e:
            log.error("Dask connection failed: %s", e)
            return False

    def submit(self, job: JobInfo) -> bool:
        if not self._connected:
            return False
        try:
            mod_name, func_name = job.func.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            future = self._client.submit(func, *job.args, **job.kwargs)
            job._dask_future = future
            job.status = JobState.RUNNING
            job.started_at = time.time()
            job.add_log(f"submitted to Dask: {job.func}")
            return True
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
            job.add_log(f"Dask submit failed: {e}")
            return False

    def check_status(self, job: JobInfo) -> str:
        if not self._connected or not job._dask_future:
            return job.status
        status = job._dask_future.status
        if status == "finished":
            try:
                job.result = job._dask_future.result()
                job.status = JobState.COMPLETED
            except Exception as e:
                job.status = JobState.FAILED
                job.error = str(e)
            job.completed_at = time.time()
        elif status == "error":
            job.status = JobState.FAILED
            try:
                job._dask_future.result()
            except Exception as e:
                job.error = str(e)
            job.completed_at = time.time()
        elif status == "cancelled":
            job.status = JobState.CANCELLED
        return job.status

    def get_result(self, job: JobInfo, timeout: float = 30.0):
        if not self._connected or not job._dask_future:
            return None
        try:
            result = job._dask_future.result(timeout=timeout)
            job.result = result
            job.status = JobState.COMPLETED
            job.completed_at = time.time()
            return result
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
            return None

    def cancel(self, job: JobInfo) -> bool:
        if not self._connected or not job._dask_future:
            return False
        try:
            job._dask_future.cancel()
            job.status = JobState.CANCELLED
            return True
        except Exception:
            return False

    def cluster_info(self) -> dict:
        if not self._connected:
            return {}
        try:
            info = self._client.scheduler_info()
            return {
                "workers": len(info.get("workers", {})),
                "total_memory": sum(
                    w.get("memory_limit", 0) for w in info.get("workers", {}).values()
                ),
                "dashboard": self._client.dashboard_link,
            }
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Built-in lightweight work queue (no external deps)
# ---------------------------------------------------------------------------

@dataclass
class QueueItem:
    job_id: str
    payload: dict
    priority: int = 0
    submitted_by: str = ""
    submitted_at: float = field(default_factory=time.time)
    status: str = "pending"
    assigned_to: str | None = None
    result: Any = None
    error: str | None = None


class BuiltinQueue:
    """Simple server-side job queue. Workers pull jobs, report results."""

    def __init__(self):
        self.queues: dict[str, list[QueueItem]] = defaultdict(list)
        self.jobs: dict[str, QueueItem] = {}  # job_id -> item
        self.workers: dict[str, dict] = {}  # worker_id -> {queues, caps, status, last_seen}
        self.results: dict[str, QueueItem] = {}  # completed job_id -> item

    def push(self, queue_name: str, job_id: str, payload: dict,
             priority: int = 0, submitted_by: str = "") -> QueueItem:
        item = QueueItem(
            job_id=job_id, payload=payload, priority=priority,
            submitted_by=submitted_by,
        )
        self.queues[queue_name].append(item)
        # Sort by priority (higher first)
        self.queues[queue_name].sort(key=lambda x: -x.priority)
        self.jobs[job_id] = item
        return item

    def pull(self, queue_name: str, worker_id: str | None = None) -> QueueItem | None:
        q = self.queues.get(queue_name, [])
        for item in q:
            if item.status == "pending":
                item.status = "running"
                item.assigned_to = worker_id
                return item
        return None

    def ack(self, job_id: str, result=None, success: bool = True,
            error: str | None = None) -> bool:
        item = self.jobs.get(job_id)
        if not item:
            return False
        item.status = "completed" if success else "failed"
        item.result = result
        item.error = error
        self.results[job_id] = item
        # Remove from queue
        for q in self.queues.values():
            try:
                q.remove(item)
            except ValueError:
                pass
        return True

    def status(self, queue_name: str | None = None) -> dict:
        if queue_name:
            q = self.queues.get(queue_name, [])
            return {
                "queue": queue_name,
                "pending": sum(1 for i in q if i.status == "pending"),
                "running": sum(1 for i in q if i.status == "running"),
                "total": len(q),
                "workers": sum(1 for w in self.workers.values()
                              if queue_name in w.get("queues", [])),
            }
        return {
            name: {
                "pending": sum(1 for i in items if i.status == "pending"),
                "running": sum(1 for i in items if i.status == "running"),
                "total": len(items),
            }
            for name, items in self.queues.items()
        }

    def register_worker(self, worker_id: str, queues: list[str] | None = None,
                         capabilities: dict | None = None):
        self.workers[worker_id] = {
            "queues": queues or [],
            "capabilities": capabilities or {},
            "status": "idle",
            "last_seen": time.time(),
        }

    def worker_heartbeat(self, worker_id: str, status: str = "idle",
                          current_job: str | None = None):
        if worker_id in self.workers:
            self.workers[worker_id]["status"] = status
            self.workers[worker_id]["last_seen"] = time.time()
            if current_job:
                self.workers[worker_id]["current_job"] = current_job

    def get_job(self, job_id: str) -> dict | None:
        item = self.jobs.get(job_id) or self.results.get(job_id)
        if not item:
            return None
        return {
            "job_id": item.job_id,
            "status": item.status,
            "payload": item.payload,
            "priority": item.priority,
            "submitted_by": item.submitted_by,
            "assigned_to": item.assigned_to,
            "result": item.result,
            "error": item.error,
        }

    def cleanup_stale_workers(self, timeout: float = 60.0):
        now = time.time()
        stale = [wid for wid, w in self.workers.items()
                 if now - w["last_seen"] > timeout]
        for wid in stale:
            # Re-queue any running jobs from stale workers
            for q_items in self.queues.values():
                for item in q_items:
                    if item.assigned_to == wid and item.status == "running":
                        item.status = "pending"
                        item.assigned_to = None
            del self.workers[wid]


# ---------------------------------------------------------------------------
# Job executor — runs on peers that accept jobs
# ---------------------------------------------------------------------------

class JobExecutor:
    """Manages local job execution using available runtimes.

    Features:
    - Single job submission (submit)
    - Batch submission (submit_batch) — fire N jobs at once
    - Map pattern (map_func) — apply function to list of inputs
    - Auto-retry on failure with configurable max_retries
    - Per-job logging via job.add_log / job.log_lines
    - Job filtering by status, tags, batch_id
    """

    def __init__(self):
        self.ray = RayRuntime()
        self.dask = DaskRuntime()
        self.jobs: dict[str, JobInfo] = {}
        self.batches: dict[str, list[str]] = {}  # batch_id -> [job_ids]
        self._monitor_task: asyncio.Task | None = None
        self._on_complete: Callable | None = None  # callback(job)

    @property
    def available_runtimes(self) -> list[str]:
        runtimes = ["builtin"]
        if self.ray.available:
            runtimes.append("ray")
        if self.dask.available:
            runtimes.append("dask")
        return runtimes

    def init_ray(self, address: str | None = None) -> bool:
        return self.ray.connect(address)

    def init_dask(self, scheduler: str | None = None) -> bool:
        return self.dask.connect(scheduler)

    async def submit(self, job_id: str, runtime: str, func: str,
                     args: list | None = None, kwargs: dict | None = None,
                     resources: dict | None = None,
                     submitted_by: str = "",
                     max_retries: int = 0,
                     tags: list[str] | None = None,
                     batch_id: str | None = None) -> JobInfo:
        job = JobInfo(
            job_id=job_id, runtime=runtime, func=func,
            args=args or [], kwargs=kwargs or {},
            resources=resources or {},
            submitted_by=submitted_by,
            max_retries=max_retries,
            tags=tags or [],
            batch_id=batch_id,
        )
        self.jobs[job_id] = job
        if batch_id:
            self.batches.setdefault(batch_id, []).append(job_id)
        job.add_log(f"submitted (runtime={runtime}, func={func})")
        log.info("Job %s submitted: %s via %s", job_id, func, runtime)

        await self._dispatch(job)
        return job

    async def _dispatch(self, job: JobInfo):
        """Dispatch a job to the appropriate runtime."""
        runtime = job.runtime
        if runtime == "ray":
            if not self.ray._connected:
                job.status = JobState.FAILED
                job.error = "Ray not connected"
                job.add_log("failed: Ray not connected")
            else:
                self.ray.submit(job)
        elif runtime == "dask":
            if not self.dask._connected:
                job.status = JobState.FAILED
                job.error = "Dask not connected"
                job.add_log("failed: Dask not connected")
            else:
                self.dask.submit(job)
        elif runtime == "builtin":
            asyncio.create_task(self._run_builtin(job))
        else:
            job.status = JobState.FAILED
            job.error = f"Unknown runtime: {runtime}"
            job.add_log(f"failed: unknown runtime {runtime}")

    async def submit_batch(self, func: str, args_list: list[list],
                           runtime: str = "builtin",
                           max_retries: int = 0,
                           tags: list[str] | None = None) -> tuple[str, list[JobInfo]]:
        """Submit multiple jobs as a batch. Returns (batch_id, jobs)."""
        batch_id = uuid.uuid4().hex[:8]
        jobs = []
        for i, args in enumerate(args_list):
            job_id = f"{batch_id}-{i}"
            job = await self.submit(
                job_id, runtime, func, args=args,
                max_retries=max_retries, tags=tags, batch_id=batch_id)
            jobs.append(job)
        log.info("Batch %s: submitted %d jobs for %s", batch_id, len(jobs), func)
        return batch_id, jobs

    async def map_func(self, func: str, inputs: list,
                       runtime: str = "builtin",
                       max_retries: int = 0) -> tuple[str, list[JobInfo]]:
        """Map a function over a list of inputs (each input becomes args=[input])."""
        return await self.submit_batch(
            func, [[x] for x in inputs], runtime=runtime,
            max_retries=max_retries, tags=["map"])

    def get_batch(self, batch_id: str) -> dict:
        """Get batch status summary."""
        job_ids = self.batches.get(batch_id, [])
        jobs = [self.jobs[jid] for jid in job_ids if jid in self.jobs]
        by_status = defaultdict(int)
        for j in jobs:
            by_status[j.status] += 1
        results = [j.result for j in jobs if j.status == JobState.COMPLETED]
        errors = [{"job_id": j.job_id, "error": j.error}
                  for j in jobs if j.status == JobState.FAILED]
        return {
            "batch_id": batch_id,
            "total": len(jobs),
            "by_status": dict(by_status),
            "results": results,
            "errors": errors,
            "all_done": all(j.status in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)
                           for j in jobs),
        }

    async def _run_builtin(self, job: JobInfo):
        """Execute a job in-process using asyncio, with retry support."""
        while True:
            try:
                job.status = JobState.RUNNING
                job.started_at = time.time()
                job.add_log(f"running (attempt {job.retries + 1})")
                mod_name, func_name = job.func.rsplit(".", 1)
                mod = importlib.import_module(mod_name)
                func = getattr(mod, func_name)
                if asyncio.iscoroutinefunction(func):
                    result = await func(*job.args, **job.kwargs)
                else:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda: func(*job.args, **job.kwargs))
                job.result = result
                job.status = JobState.COMPLETED
                job.completed_at = time.time()
                job.add_log(f"completed in {job.completed_at - job.started_at:.3f}s")
                log.info("Job %s completed: %s", job.job_id, job.func)
                if self._on_complete:
                    self._on_complete(job)
                return
            except Exception as e:
                job.retries += 1
                if job.retries <= job.max_retries:
                    job.add_log(f"failed (attempt {job.retries}), retrying: {e}")
                    log.warning("Job %s retry %d/%d: %s", job.job_id,
                               job.retries, job.max_retries, e)
                    await asyncio.sleep(min(2 ** job.retries * 0.1, 5.0))
                    continue
                job.status = JobState.FAILED
                job.error = f"{type(e).__name__}: {e}"
                job.completed_at = time.time()
                job.add_log(f"failed permanently: {e}")
                log.error("Job %s failed: %s", job.job_id, e)
                if self._on_complete:
                    self._on_complete(job)
                return

    def check_job(self, job_id: str) -> JobInfo | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        if job.runtime == "ray" and job.status == JobState.RUNNING:
            self.ray.check_status(job)
        elif job.runtime == "dask" and job.status == JobState.RUNNING:
            self.dask.check_status(job)
        return job

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.runtime == "ray":
            return self.ray.cancel(job)
        elif job.runtime == "dask":
            return self.dask.cancel(job)
        job.status = JobState.CANCELLED
        return True

    def list_jobs(self, status: str | None = None,
                  tag: str | None = None,
                  batch_id: str | None = None) -> list[dict]:
        """List jobs with optional filtering by status, tag, or batch."""
        # Refresh statuses
        for job in self.jobs.values():
            if job.status == JobState.RUNNING:
                if job.runtime == "ray":
                    self.ray.check_status(job)
                elif job.runtime == "dask":
                    self.dask.check_status(job)
        result = []
        for j in self.jobs.values():
            if status and j.status != status:
                continue
            if tag and tag not in j.tags:
                continue
            if batch_id and j.batch_id != batch_id:
                continue
            result.append(j.to_dict())
        return result

    def get_job_logs(self, job_id: str) -> list[str]:
        """Get log lines for a specific job."""
        job = self.jobs.get(job_id)
        return job.log_lines if job else []

    def stats(self) -> dict:
        """Get aggregate statistics across all jobs."""
        by_status = defaultdict(int)
        by_runtime = defaultdict(int)
        total_duration = 0.0
        completed = 0
        for j in self.jobs.values():
            by_status[j.status] += 1
            by_runtime[j.runtime] += 1
            if j.completed_at and j.started_at:
                total_duration += j.completed_at - j.started_at
                completed += 1
        return {
            "total_jobs": len(self.jobs),
            "by_status": dict(by_status),
            "by_runtime": dict(by_runtime),
            "avg_duration_s": round(total_duration / completed, 3) if completed else 0,
            "total_batches": len(self.batches),
        }

    def purge(self, before: float | None = None,
              status: str | None = None) -> int:
        """Remove completed/failed jobs. Returns count removed."""
        to_remove = []
        for jid, j in self.jobs.items():
            if status and j.status != status:
                continue
            if before and j.completed_at and j.completed_at > before:
                continue
            if j.status in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                to_remove.append(jid)
        for jid in to_remove:
            del self.jobs[jid]
        log.info("Purged %d jobs", len(to_remove))
        return len(to_remove)
