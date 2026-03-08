"""Burrow distributed computing — Ray, Dask, and built-in queue wrappers.

All runtimes are optional. Import errors are caught gracefully so peers
without Ray or Dask can still use the built-in queue.
"""

import asyncio
import importlib
import json
import time
import traceback
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    _ray_ref: Any = None
    _dask_future: Any = None

    def to_dict(self) -> dict:
        return {
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
        }


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
            return True
        except Exception:
            return False

    def submit(self, job: JobInfo) -> bool:
        if not self._connected:
            return False
        try:
            # Create a remote function from the function name
            # func should be a module.function path like "math.factorial"
            mod_name, func_name = job.func.rsplit(".", 1)
            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            remote_func = self._ray.remote(func)
            ref = remote_func.remote(*job.args, **job.kwargs)
            job._ray_ref = ref
            job.status = JobState.RUNNING
            job.started_at = time.time()
            return True
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
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
            return True
        except Exception:
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
            return True
        except Exception as e:
            job.status = JobState.FAILED
            job.error = str(e)
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
    """Manages local job execution using available runtimes."""

    def __init__(self):
        self.ray = RayRuntime()
        self.dask = DaskRuntime()
        self.jobs: dict[str, JobInfo] = {}
        self._monitor_task: asyncio.Task | None = None

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
                     submitted_by: str = "") -> JobInfo:
        job = JobInfo(
            job_id=job_id, runtime=runtime, func=func,
            args=args or [], kwargs=kwargs or {},
            resources=resources or {},
            submitted_by=submitted_by,
        )
        self.jobs[job_id] = job

        if runtime == "ray":
            if not self.ray._connected:
                job.status = JobState.FAILED
                job.error = "Ray not connected"
            else:
                self.ray.submit(job)
        elif runtime == "dask":
            if not self.dask._connected:
                job.status = JobState.FAILED
                job.error = "Dask not connected"
            else:
                self.dask.submit(job)
        elif runtime == "builtin":
            asyncio.create_task(self._run_builtin(job))
        else:
            job.status = JobState.FAILED
            job.error = f"Unknown runtime: {runtime}"

        return job

    async def _run_builtin(self, job: JobInfo):
        """Execute a job in-process using asyncio."""
        try:
            job.status = JobState.RUNNING
            job.started_at = time.time()
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
        except Exception as e:
            job.status = JobState.FAILED
            job.error = f"{type(e).__name__}: {e}"
            job.completed_at = time.time()

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

    def list_jobs(self) -> list[dict]:
        # Refresh statuses
        for job in self.jobs.values():
            if job.status == JobState.RUNNING:
                if job.runtime == "ray":
                    self.ray.check_status(job)
                elif job.runtime == "dask":
                    self.dask.check_status(job)
        return [j.to_dict() for j in self.jobs.values()]
