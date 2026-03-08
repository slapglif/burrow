"""Unit tests for burrow.distributed module."""

import asyncio
import pytest
from burrow.distributed import (
    BuiltinQueue, JobExecutor, JobInfo, JobState, RayRuntime, DaskRuntime,
)


# ---------------------------------------------------------------------------
# BuiltinQueue tests
# ---------------------------------------------------------------------------

class TestBuiltinQueue:
    def test_push_and_pull(self):
        q = BuiltinQueue()
        q.push("tasks", "j1", {"action": "build"})
        item = q.pull("tasks")
        assert item is not None
        assert item.job_id == "j1"
        assert item.payload == {"action": "build"}
        assert item.status == "running"

    def test_priority_ordering(self):
        q = BuiltinQueue()
        q.push("q", "low", {"p": "low"}, priority=1)
        q.push("q", "high", {"p": "high"}, priority=10)
        q.push("q", "mid", {"p": "mid"}, priority=5)
        item = q.pull("q")
        assert item.job_id == "high"
        item2 = q.pull("q")
        assert item2.job_id == "mid"

    def test_pull_empty_queue(self):
        q = BuiltinQueue()
        assert q.pull("nonexistent") is None

    def test_ack_success(self):
        q = BuiltinQueue()
        q.push("q", "j1", {})
        q.pull("q")
        assert q.ack("j1", result="done", success=True)
        info = q.get_job("j1")
        assert info["status"] == "completed"
        assert info["result"] == "done"

    def test_ack_failure(self):
        q = BuiltinQueue()
        q.push("q", "j1", {})
        q.pull("q")
        q.ack("j1", success=False, error="crash")
        info = q.get_job("j1")
        assert info["status"] == "failed"
        assert info["error"] == "crash"

    def test_ack_nonexistent(self):
        q = BuiltinQueue()
        assert not q.ack("nope")

    def test_status_single_queue(self):
        q = BuiltinQueue()
        q.push("q", "j1", {})
        q.push("q", "j2", {})
        q.pull("q")  # j1 now running
        s = q.status("q")
        assert s["pending"] == 1
        assert s["running"] == 1
        assert s["total"] == 2

    def test_status_all_queues(self):
        q = BuiltinQueue()
        q.push("a", "j1", {})
        q.push("b", "j2", {})
        s = q.status()
        assert "a" in s
        assert "b" in s

    def test_register_worker(self):
        q = BuiltinQueue()
        q.register_worker("w1", queues=["tasks"], capabilities={"gpu": True})
        assert "w1" in q.workers
        assert q.workers["w1"]["queues"] == ["tasks"]

    def test_worker_heartbeat(self):
        q = BuiltinQueue()
        q.register_worker("w1")
        q.worker_heartbeat("w1", status="busy", current_job="j1")
        assert q.workers["w1"]["status"] == "busy"

    def test_get_job_not_found(self):
        q = BuiltinQueue()
        assert q.get_job("nope") is None

    def test_cleanup_stale_workers(self):
        q = BuiltinQueue()
        q.register_worker("w1")
        q.workers["w1"]["last_seen"] = 0  # very old
        q.push("q", "j1", {})
        q.pull("q", "w1")  # assign to stale worker
        q.cleanup_stale_workers(timeout=1.0)
        assert "w1" not in q.workers
        # Job should be re-queued
        info = q.get_job("j1")
        assert info["status"] == "pending"


# ---------------------------------------------------------------------------
# JobExecutor tests (builtin runtime only — Ray/Dask need external deps)
# ---------------------------------------------------------------------------

class TestJobExecutor:
    def test_available_runtimes(self):
        ex = JobExecutor()
        assert "builtin" in ex.available_runtimes

    @pytest.mark.asyncio
    async def test_submit_builtin(self):
        ex = JobExecutor()
        job = await ex.submit("j1", "builtin", "math.factorial",
                              args=[5])
        # Wait for completion
        for _ in range(50):
            await asyncio.sleep(0.05)
            ex.check_job("j1")
            if job.status == JobState.COMPLETED:
                break
        assert job.status == JobState.COMPLETED
        assert job.result == 120

    @pytest.mark.asyncio
    async def test_submit_builtin_error(self):
        ex = JobExecutor()
        job = await ex.submit("j2", "builtin", "math.factorial",
                              args=[-1])
        for _ in range(50):
            await asyncio.sleep(0.05)
            ex.check_job("j2")
            if job.status in (JobState.COMPLETED, JobState.FAILED):
                break
        assert job.status == JobState.FAILED
        assert job.error is not None

    @pytest.mark.asyncio
    async def test_submit_unknown_runtime(self):
        ex = JobExecutor()
        job = await ex.submit("j3", "unknown", "math.factorial")
        assert job.status == JobState.FAILED
        assert "Unknown runtime" in job.error

    def test_list_jobs(self):
        ex = JobExecutor()
        # Just verify structure
        assert isinstance(ex.list_jobs(), list)

    def test_cancel_nonexistent(self):
        ex = JobExecutor()
        assert not ex.cancel_job("nope")

    @pytest.mark.asyncio
    async def test_cancel_builtin(self):
        ex = JobExecutor()
        job = await ex.submit("j4", "builtin", "time.sleep", args=[10])
        assert ex.cancel_job("j4")
        assert job.status == JobState.CANCELLED


# ---------------------------------------------------------------------------
# Runtime availability checks
# ---------------------------------------------------------------------------

class TestRuntimes:
    def test_ray_availability(self):
        rt = RayRuntime()
        # Just check it doesn't crash
        _ = rt.available

    def test_dask_availability(self):
        rt = DaskRuntime()
        _ = rt.available

    def test_ray_cluster_info_not_connected(self):
        rt = RayRuntime()
        assert rt.cluster_info() == {}

    def test_dask_cluster_info_not_connected(self):
        rt = DaskRuntime()
        assert rt.cluster_info() == {}


# ---------------------------------------------------------------------------
# JobInfo tests
# ---------------------------------------------------------------------------

class TestJobInfo:
    def test_to_dict(self):
        job = JobInfo(job_id="j1", runtime="builtin", func="math.factorial")
        d = job.to_dict()
        assert d["job_id"] == "j1"
        assert d["runtime"] == "builtin"
        assert d["func"] == "math.factorial"
        assert d["status"] == JobState.PENDING
        assert d["result"] is None

    def test_job_state_values(self):
        assert JobState.PENDING == "pending"
        assert JobState.RUNNING == "running"
        assert JobState.COMPLETED == "completed"
        assert JobState.FAILED == "failed"
        assert JobState.CANCELLED == "cancelled"
