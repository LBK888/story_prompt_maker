import asyncio

import pytest

from app.config import AppConfig
from app.generation import GenerationError, GenerationManager


class StoreStub:
    def load(self, project_id):
        return {"segments": [{"id": "segment_1", "continuity_from_previous": False},
                             {"id": "segment_2", "continuity_from_previous": False}]}

    def list(self, include_archived=False):
        return [{"id": "project_1", "segments": [{"id": "segment_1", "takes": [
            {"id": "run_saved", "status": "running", "prompt_id": "comfy_saved", "created_at": "2026-01-01"}]}]}]


class EventsStub:
    async def publish(self, project_id, event):
        pass


def test_remote_workflows_submit_without_waiting_for_previous_result():
    async def scenario():
        manager = GenerationManager(StoreStub(), None, None, AppConfig(comfy_url="https://remote.example:8188"), EventsStub())
        calls = []
        monitors = []

        async def fake_generate(project_id, segment_id, defer_wait=False):
            calls.append((segment_id, defer_wait))
            return {"id": f"run_{segment_id}", "prompt_id": f"comfy_{segment_id}"}

        manager.generate_segment = fake_generate
        manager._start_monitor = lambda job_id: monitors.append(job_id)
        ids = manager.enqueue("project_1", ["segment_1", "segment_2"])
        await manager._worker_task
        assert calls == [("segment_1", True), ("segment_2", True)]
        assert monitors == ids
        assert [manager.jobs[id]["prompt_id"] for id in ids] == ["comfy_segment_1", "comfy_segment_2"]
        assert all(manager.jobs[id]["status"] == "submitted" for id in ids)

    asyncio.run(scenario())


def test_restart_rebuilds_job_from_saved_comfy_prompt_id():
    async def scenario():
        manager = GenerationManager(StoreStub(), None, None, AppConfig(comfy_url="https://remote.example:8188"), EventsStub())
        monitors = []
        manager._start_monitor = lambda job_id: monitors.append(job_id)
        manager.resume_submitted()
        job = manager.jobs["resume_run_saved"]
        assert job["prompt_id"] == "comfy_saved"
        assert job["status"] == "submitted"
        assert monitors == ["resume_run_saved"]

    asyncio.run(scenario())


def test_resume_attaches_to_in_flight_submission_without_duplicate_job():
    async def scenario():
        manager = GenerationManager(StoreStub(), None, None, AppConfig(comfy_url="https://remote.example:8188"), EventsStub())
        manager.jobs["job_original"] = {"id": "job_original", "project_id": "project_1",
                                        "segment_id": "segment_1", "status": "running", "sequence": 1}
        monitors = []
        manager._start_monitor = lambda job_id: monitors.append(job_id)
        manager.resume_submitted()
        assert list(manager.jobs) == ["job_original"]
        assert manager.jobs["job_original"]["run_id"] == "run_saved"
        assert manager.jobs["job_original"]["status"] == "submitted"
        assert monitors == ["job_original"]

    asyncio.run(scenario())


def test_completed_take_leaves_queue_while_another_is_still_running():
    class Store:
        def load(self, project_id):
            return {"segments": [{"takes": [{"id": "run_1", "status": "completed"},
                                             {"id": "run_2", "status": "running"}]}]}

    manager = GenerationManager(Store(), None, None, AppConfig(), EventsStub())
    manager.jobs = {
        "job_1": {"id": "job_1", "project_id": "project_1", "run_id": "run_1", "status": "submitted", "sequence": 1},
        "job_2": {"id": "job_2", "project_id": "project_1", "run_id": "run_2", "status": "submitted", "sequence": 2},
    }
    assert [job["id"] for job in manager.list_jobs("project_1")] == ["job_2"]
    assert manager.jobs["job_1"]["status"] == "completed"


def test_remote_pending_job_is_deleted_by_prompt_id_and_does_not_resume(monkeypatch):
    class Store:
        def __init__(self):
            self.project = {"id": "project_1", "segments": [{"id": "segment_1", "status": "running",
                "takes": [{"id": "run_1", "status": "running", "prompt_id": "prompt_1"}]}]}

        def load(self, project_id):
            return self.project

        def save(self, project):
            self.project = project

    class Client:
        deleted = []

        def __init__(self, base_url):
            pass

        async def history(self, prompt_id):
            return None

        async def queue_state(self, prompt_id):
            return ("unknown", None) if prompt_id in self.deleted else ("queued", 1)

        async def delete_queued(self, prompt_id):
            self.deleted.append(prompt_id)

    async def scenario():
        monkeypatch.setattr("app.generation.ComfyClient", Client)
        store = Store()
        manager = GenerationManager(store, None, None, AppConfig(), EventsStub())
        manager.jobs["job_1"] = {"id": "job_1", "project_id": "project_1", "segment_id": "segment_1",
                                 "run_id": "run_1", "prompt_id": "prompt_1", "status": "submitted"}
        assert (await manager.cancel_job("job_1"))["status"] == "canceled"
        assert Client.deleted == ["prompt_1"]
        assert store.project["segments"][0]["takes"][0]["status"] == "canceled"
        assert store.project["segments"][0]["status"] == "planned"
        assert manager.list_jobs("project_1") == []

    asyncio.run(scenario())


def test_remote_running_job_cannot_be_canceled(monkeypatch):
    class Client:
        def __init__(self, base_url):
            pass

        async def history(self, prompt_id):
            return None

        async def queue_state(self, prompt_id):
            return "running", None

    async def scenario():
        monkeypatch.setattr("app.generation.ComfyClient", Client)
        store = StoreStub()
        manager = GenerationManager(store, None, None, AppConfig(), EventsStub())
        manager.jobs["job_1"] = {"id": "job_1", "project_id": "project_1", "segment_id": "segment_1",
                                 "run_id": "run_saved", "prompt_id": "comfy_saved", "status": "submitted"}
        with pytest.raises(GenerationError, match="處理中"):
            await manager.cancel_job("job_1")
        assert manager.jobs["job_1"]["status"] == "submitted"

    asyncio.run(scenario())


def test_remotely_removed_job_can_be_cleared_locally(monkeypatch):
    class Store:
        def __init__(self):
            self.project = {"id": "project_1", "segments": [{"id": "segment_1", "status": "running",
                "takes": [{"id": "run_1", "status": "running", "prompt_id": "prompt_1"}]}]}

        def load(self, project_id):
            return self.project

        def save(self, project):
            self.project = project

    class Client:
        def __init__(self, base_url):
            pass

        async def history(self, prompt_id):
            return None

        async def queue_state(self, prompt_id):
            return "unknown", None

        async def delete_queued(self, prompt_id):
            raise AssertionError("A missing prompt must not be deleted again")

    async def scenario():
        monkeypatch.setattr("app.generation.ComfyClient", Client)
        store = Store()
        manager = GenerationManager(store, None, None, AppConfig(), EventsStub())
        manager.jobs["job_1"] = {"id": "job_1", "project_id": "project_1", "segment_id": "segment_1",
                                 "run_id": "run_1", "prompt_id": "prompt_1", "status": "submitted"}
        assert (await manager.cancel_job("job_1"))["status"] == "canceled"
        assert store.project["segments"][0]["takes"][0]["status"] == "canceled"
        assert manager.list_jobs("project_1") == []

    asyncio.run(scenario())
