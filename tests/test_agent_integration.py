import asyncio
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from app.agent_client import AgentClient, AgentClientError
from app.authoring import Authoring
from app.cli import execute, parser
from app.comfy_client import ComfyOutput
from app.config import AppConfig
from app.events import EventBroker
from app.generation import GenerationManager
from app.segment_images import SegmentImageService
from app.storage import ProjectStore
from app.template_catalog import TemplateCatalog
from app.template_engine import load_template
from test_agent_authoring import blueprint, PROMPT


class NoLlm:
    def inference_status(self):
        return {"busy": False}

    def __getattr__(self, name):
        raise AssertionError(f"Agent pipeline accessed built-in LLM: {name}")


@pytest.fixture
def backend(tmp_path, monkeypatch):
    import app.main as main

    store = ProjectStore(tmp_path / "projects")
    config = AppConfig(execution_mode="agent")
    llama = NoLlm()
    templates = TemplateCatalog(store)
    path = tmp_path / "agent-video.json"
    path.write_text(json.dumps({"1": {"class_type": "TestVideo", "inputs": {
        "prompt": "[[PROMPT]]", "seed": "[[SEED]]", "duration": "[[DURATION]]",
        "prefix": "[[OUTPUT_PREFIX]]"}}}), encoding="utf-8")
    template = load_template(path)
    monkeypatch.setattr(templates, "list", lambda: ([template], {}))
    manager = GenerationManager(store, templates, llama, config, EventBroker())
    manager.operations.update(main.generation.operations)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "config", config)
    monkeypatch.setattr(main, "llama", llama)
    monkeypatch.setattr(main, "template_catalog", templates)
    monkeypatch.setattr(main, "generation", manager)
    monkeypatch.setattr(main, "segment_images", SegmentImageService(store, llama, config))

    class Comfy:
        submitted = []
        histories = {}

        def __init__(self, url):
            pass

        async def health(self):
            return {}

        async def submit(self, workflow):
            self.submitted.append(workflow)
            node = next(n for n in workflow.values() if n["class_type"] in {"TestVideo", "SaveImageAdvanced"})
            still = node["class_type"] == "SaveImageAdvanced"
            prefix = node["inputs"]["filename_prefix" if still else "prefix"]
            prompt_id = f"comfy_{len(self.submitted)}"
            self.histories[prompt_id] = {"media": [ComfyOutput(
                filename="image.png" if still else "video.mp4", subfolder=prefix,
                output_type="output", media_type="images" if still else "videos")]}
            return prompt_id

        async def wait(self, prompt_id):
            return self.histories[prompt_id]

        def outputs(self, history):
            return history["media"]

        def matching_outputs(self, outputs, prefix):
            return [o for o in outputs if o.relative_name.startswith(prefix)]

        async def download(self, output, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if output.media_type == "images":
                Image.new("RGB", (16, 16), "orange").save(destination)
            else:
                destination.write_bytes(b"simulated video output")

        async def upload_input(self, path, remote_name):
            return {"name": Path(remote_name).name, "subfolder": "input"}

    async def duration(*args):
        return 4.0

    async def concatenate(ffmpeg, videos, destination, width, height):
        assert len(videos) == 2 and all(v.is_file() for v in videos)
        destination.write_bytes(b"simulated merged video")

    monkeypatch.setattr("app.generation.ComfyClient", Comfy)
    monkeypatch.setattr("app.segment_images.ComfyClient", Comfy)
    monkeypatch.setattr("app.generation.probe_duration", duration)
    monkeypatch.setattr(main, "probe_duration", duration)
    monkeypatch.setattr(main, "concatenate", concatenate)
    return main, store, manager, Comfy


async def completed(client, job_id):
    for _ in range(100):
        job = await client.request("GET", f"/api/agent/jobs/{job_id}")
        if job["status"] in {"completed", "failed", "canceled"}:
            assert job["status"] == "completed", job
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_full_agent_pipeline_images_video_merge_and_idempotent_retries(backend, tmp_path):
    main, store, manager, comfy = backend

    async def scenario():
        client = AgentClient("http://test", transport=httpx.ASGITransport(app=main.app))
        project = await client.request("POST", "/api/projects", json={"name": "Pipeline", "story": "A painter opens a window."})
        pid = project["id"]
        root = f"/api/agent/projects/{pid}"
        project = await client.request("PATCH", root + "/settings", json={"expected_revision": project["revision"], "changes": {"minimum_duration_seconds": 8}})
        context = await client.context(pid, "blueprint")
        result = await client.apply_blueprint(pid, blueprint(), context["revision"])
        revision = result["revision"]

        # Local file upload and real image previews exercise the multipart adapter.
        reference = tmp_path / "reference.png"
        Image.new("RGB", (32, 24), "purple").save(reference)
        asset = await client.import_asset(pid, str(reference))
        project = store.load(pid)
        await client.request("PATCH", root + f"/assets/{asset['id']}", json={"expected_revision": project["revision"], "changes": {"roles": ["style"]}})
        context = await client.context(pid, "blueprint")
        assert context["references"][0]["id"] == asset["id"]
        response = await client.response("GET", context["references"][0]["preview_url"])
        assert response.headers["content-type"].startswith("image/")

        # A still render must not invoke the former LLM filename generator.
        revision = store.load(pid)["revision"]
        still = await client.submit(pid, revision, "still-one", "image", segment_id="segment_001",
                                    image_kind="scene", prompt="A room lit by sunrise", output_title="sunrise")
        still_job = await completed(client, still["jobs"][0]["id"])
        assert len(still_job["result"]["candidates"]) == 1
        assert "sunrise" in still_job["result"]["candidates"][0]["filename"]

        revision = store.load(pid)["revision"]
        for sid in ("segment_001", "segment_002"):
            assigned = await client.request("PUT", root + f"/segments/{sid}/template", json={
                "expected_revision": revision, "template_name": "agent-video"})
            revision = assigned["revision"]
        for sid in ("segment_001", "segment_002"):
            result = await client.set_prompt(pid, sid, PROMPT, revision)
            revision = result["revision"]
        submitted = await client.submit(pid, revision, "video-one")
        assert len(submitted["jobs"]) == 2
        retry = await client.submit(pid, revision, "video-one")
        assert retry["replayed"]
        assert [j["id"] for j in retry["jobs"]] == [j["id"] for j in submitted["jobs"]]
        with pytest.raises(AgentClientError) as error:
            await client.submit(pid, revision, "video-one", segment_ids=["segment_001"])
        assert error.value.status == 409
        for job in submitted["jobs"]:
            await completed(client, job["id"])
        assert len(comfy.submitted) == 3
        merged = await client.submit(pid, store.load(pid)["revision"], "merge-one", "merge")
        result = await completed(client, merged["jobs"][0]["id"])
        assert result["result"]["path"].endswith(".mp4")
        artifacts = await client.request("GET", root + "/artifacts")
        assert len(artifacts["final_outputs"]) == 1
        assert all(s["takes"][0]["url"] for s in artifacts["segments"])

        # Retry after recreating the queue manager: no second remote generation.
        restored = GenerationManager(store, main.template_catalog, main.llama, main.config, EventBroker())
        restored.operations.update(manager.operations)
        monkeypatch_generation = main.generation
        main.generation = restored
        try:
            replay = await client.submit(pid, 0, "video-one")
            assert replay["replayed"]
            assert len(comfy.submitted) == 3
        finally:
            main.generation = monkeypatch_generation
            await restored.shutdown()
        await manager.shutdown()

    asyncio.run(scenario())


def test_cli_routes_authoring_and_preserves_structured_errors(backend, tmp_path):
    main, store, manager, _ = backend
    project = store.create("CLI", "Story")
    project["settings"]["minimum_duration_seconds"] = 8
    store.save(project)
    file = tmp_path / "blueprint.json"
    file.write_text(json.dumps(blueprint()), encoding="utf-8")

    async def scenario():
        client = AgentClient("http://test", transport=httpx.ASGITransport(app=main.app))
        context = await execute(parser().parse_args(["context", "--project", project["id"], "--task", "blueprint"]), client)
        args = parser().parse_args(["blueprint", "apply", "--project", project["id"], "--file", str(file),
                                    "--expected-revision", str(context["revision"])])
        result = await execute(args, client)
        assert len(result["project"]["segments"]) == 2
        with pytest.raises(AgentClientError) as error:
            await execute(args, client)
        assert error.value.detail["code"] == "revision_conflict"
    asyncio.run(scenario())


def test_mcp_protocol_tools_images_and_error_results(backend, tmp_path):
    pytest.importorskip("mcp")
    from mcp.shared.memory import create_connected_server_and_client_session
    from app.mcp_server import create_server
    main, store, _, _ = backend
    project = store.create("MCP", "Story")
    project["settings"]["minimum_duration_seconds"] = 8
    store.save(project)
    author = Authoring(store)
    author.apply_blueprint(project["id"], blueprint(), project["revision"])
    image = tmp_path / "reference.png"
    Image.new("RGB", (20, 20), "purple").save(image)
    asset = store.import_asset(project["id"], image, image.name)

    async def scenario():
        server = create_server(AgentClient("http://test", transport=httpx.ASGITransport(app=main.app)))
        async with create_connected_server_and_client_session(server) as session:
            listed = await session.list_tools()
            assert {"get_authoring_context", "apply_blueprint", "submit_generation", "get_reference_image"}.issubset({t.name for t in listed.tools})
            result = await session.call_tool("get_authoring_context", {"project_id": project["id"], "task": "prompt", "segment_id": "segment_001"})
            assert not result.isError
            assert result.structuredContent["inference"] == "caller"
            invalid = await session.call_tool("set_segment_prompt", {"project_id": project["id"], "segment_id": "segment_001", "prompt": "bad", "expected_revision": result.structuredContent["revision"]})
            assert invalid.isError
            assert "invalid_prompt" in invalid.content[0].text
            preview = await session.call_tool("get_reference_image", {"project_id": project["id"], "asset_id": asset["id"]})
            assert not preview.isError
            assert preview.content[0].type == "image"
            guide = await session.read_resource("vap://agent-guide")
            assert "caller" in guide.contents[0].text
    asyncio.run(scenario())
