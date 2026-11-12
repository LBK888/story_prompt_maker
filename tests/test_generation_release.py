import asyncio
from pathlib import Path

import pytest

from app.config import AppConfig
from app.generation import GenerationError, GenerationManager


class LlamaStub:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> bool:
        self.stopped = True
        return True

    def running_pid(self) -> None:
        return None

    async def health(self) -> bool:
        return False


class EventsStub:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def publish(self, project_id: str, event: dict) -> None:
        self.items.append(event)


def test_shared_gpu_release_checks_only_managed_llama_process() -> None:
    llama = LlamaStub()
    events = EventsStub()
    config = AppConfig(comfy_shared_gpu=True)
    manager = GenerationManager(None, None, llama, config, events)  # type: ignore[arg-type]

    asyncio.run(manager._release_llm_if_needed("project_1", "segment_1"))

    assert llama.stopped is True
    assert [item["kind"] for item in events.items] == ["generation_progress", "generation_progress"]


def test_external_llm_does_not_run_local_vram_release_checks() -> None:
    llama = LlamaStub()
    llama.config = AppConfig(llm_provider="openrouter")
    events = EventsStub()
    manager = GenerationManager(None, None, llama, llama.config, events)  # type: ignore[arg-type]

    asyncio.run(manager._release_llm_if_needed("project_1", "segment_1"))

    assert llama.stopped is False
    assert len(events.items) == 1
    assert "外部 API" in events.items[0]["message"]


def test_generation_module_does_not_gate_comfy_on_global_vram() -> None:
    source = Path("app/generation.py").read_text(encoding="utf-8")
    assert "wait_for_vram_release" not in source
    assert "vram_release_threshold_mb" not in source


def test_each_segment_is_an_independent_cancelable_queue_item() -> None:
    async def scenario() -> None:
        manager = GenerationManager(None, None, LlamaStub(), AppConfig(), EventsStub())  # type: ignore[arg-type]
        release = asyncio.Event()
        calls: list[str] = []

        async def generate_segment(project_id: str, segment_id: str) -> None:
            calls.append(segment_id)
            if segment_id == "segment_1":
                await release.wait()

        manager.generate_segment = generate_segment  # type: ignore[method-assign]
        job_ids = manager.enqueue("project_1", ["segment_1", "segment_2", "segment_3"])
        worker = manager._worker_task
        await asyncio.sleep(0)

        assert len(job_ids) == 3
        assert manager.jobs[job_ids[0]]["status"] == "running"
        assert manager.jobs[job_ids[1]]["status"] == "queued"
        assert manager.cancel(job_ids[2])["status"] == "canceled"
        with pytest.raises(GenerationError, match="still waiting"):
            manager.cancel(job_ids[0])

        release.set()
        assert worker is not None
        await worker
        assert calls == ["segment_1", "segment_2"]
        assert manager.jobs[job_ids[1]]["status"] == "completed"

    asyncio.run(scenario())


def test_same_segment_can_be_queued_more_than_once() -> None:
    async def scenario() -> None:
        manager = GenerationManager(None, None, LlamaStub(), AppConfig(), EventsStub())  # type: ignore[arg-type]
        calls: list[str] = []

        async def generate_segment(project_id: str, segment_id: str) -> None:
            calls.append(segment_id)

        manager.generate_segment = generate_segment  # type: ignore[method-assign]
        first = manager.enqueue("project_1", ["segment_1"])
        second = manager.enqueue("project_1", ["segment_1"])
        worker = manager._worker_task
        assert first != second
        assert len(manager.jobs) == 2
        assert worker is not None
        await worker
        assert calls == ["segment_1", "segment_1"]

    asyncio.run(scenario())


def test_active_job_is_scoped_to_the_requested_project() -> None:
    manager = GenerationManager(None, None, LlamaStub(), AppConfig(), EventsStub())  # type: ignore[arg-type]
    manager.jobs = {
        "completed_other": {"id": "completed_other", "project_id": "other", "status": "completed"},
        "active_target": {"id": "active_target", "project_id": "target", "status": "running"},
    }

    assert manager.active_job("target")["id"] == "active_target"
    assert manager.has_active_job("target") is True
    assert manager.has_active_job("other") is False
