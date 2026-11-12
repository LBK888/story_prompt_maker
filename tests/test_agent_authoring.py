import asyncio
import json
from copy import deepcopy

import pytest

from app.authoring import Authoring, AuthoringError
from app.config import AppConfig
from app.events import EventBroker
from app.generation import GenerationManager
from app.llama_service import LlamaServerError, LlamaServerManager
from app.storage import ProjectStore
from app.project_archive import export_project, import_project


PROMPT = """integrated_multimodal_description: [Shot 1] A painter stands beside an open window in warm sunrise light.

overall_soundscape: Quiet room tone and birds outside.

non_diegetic_music: N/A"""


def blueprint():
    return {"title": "Sunrise", "story_summary": "A painter opens a window.",
            "continuity_bible": {"characters": [], "locations": [], "global_visual_rules": []},
            "sound_bible": {"ambience": "Birds", "music": "None", "continuity": "Same room"},
            "segments": [{"id": f"segment_{i:03d}", "title": title,
                          "summary": f"The painter {title.lower()} in the room.",
                          "duration_seconds": 4, "task_type": "T2VA",
                          "shots": [{"start_seconds": 0, "framing": "medium close-up",
                                     "visual": "A painter beside a window", "action": title,
                                     "camera": "A slow push in", "audio": "Birds", "speaker_ids": []}],
                          "dialogue": [], "continuity_from_previous": False, "reference_asset_ids": []}
                         for i, title in enumerate(("Opens the window", "Looks outside"), 1)]}


@pytest.fixture
def authoring(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Sunrise", "A painter opens a window.")
    project["settings"]["minimum_duration_seconds"] = 8
    store.save(project)
    return Authoring(store), project["id"]


def test_context_schema_and_rejected_blueprints_preserve_existing_state(authoring):
    author, pid = authoring
    context = author.context(pid, "blueprint")
    assert context["inference"] == "caller"
    assert "id" in context["output_schema"]["properties"]["segments"]["items"]["properties"]
    for mutate in (lambda b: b["segments"][0].update(takes=[{"id": "injected"}]),
                   lambda b: b["segments"][0].update(duration_seconds=26),
                   lambda b: b["segments"][0].update(reference_asset_ids=["asset_missing"]),
                   lambda b: b["segments"][1].update(id="segment_001"),
                   lambda b: b["segments"][0]["shots"][0].update(start_seconds=4)):
        raw = blueprint()
        mutate(raw)
        before = author.store.project_file(pid).read_bytes()
        with pytest.raises(AuthoringError):
            author.apply_blueprint(pid, raw, context["revision"])
        assert author.store.project_file(pid).read_bytes() == before
    result = author.apply_blueprint(pid, blueprint(), context["revision"], validate_only=True)
    assert result["valid"]
    assert author.store.load(pid)["segments"] == []


def test_updates_preserve_execution_data_and_stale_survives_reload(authoring):
    author, pid = authoring
    initial = author.apply_blueprint(pid, blueprint(), author.store.load(pid)["revision"])["project"]
    segment = initial["segments"][0]
    segment.update(takes=[{"id": "take_keep", "status": "completed"}], active_take_id="take_keep",
                   template={"name": "saved-workflow"}, image_candidates=[{"id": "image_keep"}])
    author.store.save(initial)
    result = author.set_prompt(pid, segment["id"], PROMPT, initial["revision"])
    updated = blueprint()
    updated["segments"][0]["summary"] = "The painter turns toward a vase."
    result = author.apply_blueprint(pid, updated, result["revision"])
    saved = author.store.load(pid)["segments"][0]
    assert saved["takes"] == segment["takes"]
    assert saved["active_take_id"] == "take_keep"
    assert saved["template"] == {"name": "saved-workflow"}
    assert saved["image_candidates"] == [{"id": "image_keep"}]
    assert saved["prompt"] == PROMPT
    assert saved["prompt_status"] == "stale"
    with pytest.raises(AuthoringError, match="retain"):
        author.apply_blueprint(pid, {**updated, "segments": updated["segments"][:1]}, result["revision"])


def test_prompt_validation_and_revision_conflicts_cannot_bypass_checks(authoring):
    author, pid = authoring
    initial = author.apply_blueprint(pid, blueprint(), author.store.load(pid)["revision"])
    revision = initial["revision"]
    with pytest.raises(AuthoringError) as error:
        author.set_prompt(pid, "segment_001", "plain invalid prompt", revision)
    assert error.value.detail["errors"][0]["path"] == "/prompt"
    assert author.store.load(pid)["revision"] == revision
    result = author.set_prompt(pid, "segment_001", PROMPT, revision)
    assert result["segment"]["prompt_source"] == "agent"
    with pytest.raises(AuthoringError) as conflict:
        author.set_prompt(pid, "segment_001", PROMPT, revision)
    assert conflict.value.status == 409
    assert conflict.value.detail["current_revision"] == result["revision"]
    assert author.store.load(pid)["segments"][0]["prompt_status"] == "ready"


def test_each_segment_requires_selected_speech_and_correct_language(authoring):
    author, pid = authoring
    project = author.store.load(pid)
    project["settings"].update(create_dialogue=True, create_narration=True)
    author.store.save(project)
    raw = blueprint()
    for segment in raw["segments"]:
        segment["dialogue"] = [
            {"speaker_id": "S1", "kind": "dialogue", "language": "Chinese", "original_text": "早安。", "spoken_text": "早安。", "estimated_seconds": 0},
            {"speaker_id": "S2", "kind": "voiceover", "language": "Chinese", "original_text": "天亮了。", "spoken_text": "天亮了。", "estimated_seconds": 0}]
    raw["segments"][1]["dialogue"][1]["kind"] = "dialogue"
    with pytest.raises(AuthoringError) as error:
        author.apply_blueprint(pid, raw, project["revision"])
    assert any(e["code"] == "missing_speech" for e in error.value.detail["errors"])


def test_agent_mode_disables_all_llm_providers_before_network_or_process_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Agent mode touched an LLM process or network")

    async def scenario():
        for provider in ("local", "ollama", "openai", "openrouter", "custom"):
            manager = LlamaServerManager(AppConfig(execution_mode="agent", llm_provider=provider))
            monkeypatch.setattr(manager, "_managed_listener_pid", forbidden)
            monkeypatch.setattr(manager, "_local_health", forbidden)
            monkeypatch.setattr(manager, "_request_headers", forbidden)
            assert manager.preflight() == []
            assert manager.running_pid() is None
            assert not await manager.health()
            assert not await manager.stop()
            with pytest.raises(LlamaServerError, match="disabled"):
                await manager.ensure_started()
            with pytest.raises(LlamaServerError, match="disabled"):
                await manager.completion([])
            with pytest.raises(LlamaServerError, match="disabled"):
                async with manager.operation("test"):
                    raise AssertionError("Entered disabled inference context")

    asyncio.run(scenario())


def test_pending_jobs_and_cancellation_survive_restart(authoring, monkeypatch):
    author, pid = authoring
    async def scenario():
        first = GenerationManager(author.store, None, None, AppConfig(execution_mode="agent"), EventBroker())
        monkeypatch.setattr(first, "_ensure_worker", lambda: None)
        jobs = first.enqueue(pid, ["segment_001", "segment_002"], metadata={"request_id": "request-one"})
        first.cancel(jobs[1])
        second = GenerationManager(author.store, None, None, AppConfig(execution_mode="agent"), EventBroker())
        monkeypatch.setattr(second, "_ensure_worker", lambda: None)
        second.resume_submitted(pid)
        assert list(second._pending) == [jobs[0]]
        assert second.jobs[jobs[1]]["status"] == "canceled"
        assert second.jobs[jobs[0]]["request_id"] == "request-one"

    asyncio.run(scenario())


def test_restart_reattaches_saved_video_with_same_public_job_id(authoring, monkeypatch):
    author, pid = authoring
    project = author.apply_blueprint(pid, blueprint(), author.store.load(pid)["revision"])["project"]
    project["generation_jobs"] = [{"id": "job_keep", "project_id": pid, "segment_id": "segment_001",
                                    "sequence": 1, "status": "running", "request_id": "retry-key"}]
    project["segments"][0]["takes"] = [{"id": "run_keep", "job_id": "job_keep", "prompt_id": "comfy_keep", "status": "running"}]
    author.store.save(project)
    async def scenario():
        manager = GenerationManager(author.store, None, None, AppConfig(), EventBroker())
        monitors = []
        monkeypatch.setattr(manager, "_start_monitor", monitors.append)
        manager.resume_submitted(pid)
        assert set(manager.jobs) == {"job_keep"}
        assert manager.jobs["job_keep"]["status"] == "submitted"
        assert manager.jobs["job_keep"]["prompt_id"] == "comfy_keep"
        assert monitors == ["job_keep"]
    asyncio.run(scenario())


def test_restart_does_not_resubmit_ambiguous_render(authoring):
    author, pid = authoring
    project = author.store.load(pid)
    project["generation_jobs"] = [{"id": "job_ambiguous", "project_id": pid, "segment_id": "segment_001",
                                    "sequence": 1, "status": "running", "request_id": "retry-key"}]
    author.store.save(project)
    async def scenario():
        manager = GenerationManager(author.store, None, None, AppConfig(), EventBroker())
        manager.resume_submitted(pid)
        assert manager.jobs["job_ambiguous"]["status"] == "failed"
        assert not manager._pending
    asyncio.run(scenario())


def test_imported_project_does_not_resume_original_queue(authoring, tmp_path):
    author, pid = authoring
    project = author.store.load(pid)
    project["generation_jobs"] = [{"id": "job_source", "project_id": pid, "segment_id": "segment_001",
                                    "sequence": 1, "status": "queued", "request_id": "original-request"}]
    author.store.save(project)
    archive = tmp_path / "project.zip"
    export_project(author.store, pid, archive)
    imported = import_project(author.store, archive)
    job = imported["generation_jobs"][0]
    assert job["id"] != "job_source"
    assert job["project_id"] == imported["id"] != pid
    assert job["status"] == "failed"
    assert author.store.load(pid)["generation_jobs"][0]["status"] == "queued"


def test_agent_mode_is_not_persisted_when_saving_machine_settings(tmp_path, monkeypatch):
    import app.config as config_module
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "app.json")
    config_module.save_config(AppConfig(execution_mode="agent", comfy_url="http://127.0.0.1:9999"))
    saved = json.loads((tmp_path / "app.json").read_text(encoding="utf-8"))
    assert "execution_mode" not in saved
    assert saved["comfy_url"] == "http://127.0.0.1:9999"
