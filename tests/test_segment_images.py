import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from app.blank_storyboard import _blank_segment
from app.comfy_client import ComfyOutput
from app.config import AppConfig
from app.domain import ASPECT_RATIOS
from app.segment_images import SegmentImageService, image_dimensions
from app.storage import ProjectStore
from app.timing import dimensions_for_ratio


def test_image_dimensions_follow_project_ratio_and_exceed_largest_video():
    for ratio in ASPECT_RATIOS:
        width, height = image_dimensions(ratio)
        video_width, video_height = dimensions_for_ratio(ratio, 2.0)
        assert width % 8 == height % 8 == 0
        assert 2_100_000 <= width * height <= 2_350_000
        assert width * height > video_width * video_height
        expected = ASPECT_RATIOS[ratio][0] / ASPECT_RATIOS[ratio][1]
        assert abs(width / height - expected) < 0.01


def test_llm_filename_title_is_sanitized_and_limited(tmp_path):
    class FakeLlama:
        @asynccontextmanager
        async def operation(self, name):
            yield

        async def completion(self, messages, mode="strict"):
            return '名稱：海邊畫家與夕陽下的長長街道.png'

    service = SegmentImageService(ProjectStore(tmp_path / "projects"), FakeLlama(), AppConfig())
    title = asyncio.run(service._filename_title(
        {"id": "segment_001", "summary": "sunset", "shots": []}, "scene", "A painter at sunset", []
    ))
    assert title == "海邊畫家與夕陽下的長"
    assert len(title) == 10


def test_generated_still_becomes_project_asset_and_replaces_segment_frame(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Stills")
    project_id = project["id"]
    segment = _blank_segment(1, 15, "segment_001")
    segment["summary"] = "A woman stands by a window"
    segment["shots"] = [{"visual": "Woman beside a window", "start_seconds": 0}]
    segment["template"] = {"name": "video", "first_frame_asset_id": None, "last_frame_asset_id": None}
    project["segments"] = [segment]
    store.save(project)
    submitted = []
    seeds = iter((101, 202, 303, 404))
    monkeypatch.setattr("app.segment_images.secrets.randbelow", lambda upper: next(seeds))

    class FakeLlama:
        @asynccontextmanager
        async def operation(self, name):
            yield

        async def completion(self, messages, mode="strict"):
            return "窗邊女子"

    class FakeClient:
        def __init__(self, url):
            pass

        async def health(self):
            return {}

        async def upload_input(self, path, remote_name):
            return {"name": Path(remote_name).name, "subfolder": "input"}

        async def submit(self, workflow):
            submitted.append(workflow)
            self.prefix = next(node["inputs"]["filename_prefix"] for node in workflow.values()
                               if node["class_type"] == "SaveImageAdvanced")
            return f"prompt-{len(submitted)}"

        async def wait(self, prompt_id):
            return {"outputs": {}}

        def outputs(self, history):
            return [ComfyOutput(filename="image_00001.png", subfolder=self.prefix, output_type="output", media_type="images")]

        async def download(self, output, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), {1: "purple", 2: "orange", 3: "green", 4: "blue"}[len(submitted)]).save(destination)

    monkeypatch.setattr("app.segment_images.ComfyClient", FakeClient)
    async def scenario():
        service = SegmentImageService(store, FakeLlama(), AppConfig())
        first = await service.generate(project_id, segment["id"], "first_frame", [], "A woman by a window, 16:9")
        assert first["filename"] == "seg001_窗邊女子_frame.png"
        assert first["seed"] == 101
        assert store.load(project_id)["assets"] == []
        adopted_first = service.adopt(project_id, segment["id"], first["id"])
        assert adopted_first["asset"]["id"] == store.load(project_id)["segments"][0]["template"]["first_frame_asset_id"]
        assert adopted_first["asset"]["filename"] == first["filename"]
        assert not any(node["class_type"] == "LoadImage" for node in submitted[0].values())
        assert all(node["inputs"]["batch_size"] == 1 for node in submitted[0].values() if node["class_type"] == "EmptyLatentImage")
        second = await service.generate(project_id, segment["id"], "last_frame", [adopted_first["asset"]["id"]], "Keep Reference Image 1")
        assert second["filename"] == "seg001_窗邊女子_frame(1).png"
        assert second["seed"] == 202
        assert any(node["class_type"] == "LoadImage" for node in submitted[1].values())
        assert store.load(project_id)["segments"][0]["template"]["last_frame_asset_id"] is None
        adopted_second = service.adopt(project_id, segment["id"], second["id"])
        assert store.load(project_id)["segments"][0]["template"]["last_frame_asset_id"] == adopted_second["asset"]["id"]
        replacement = await service.generate(project_id, segment["id"], "first_frame", [], "A new composition")
        assert replacement["seed"] == 303
        adopted_replacement = service.adopt(project_id, segment["id"], replacement["id"])
        saved = store.load(project_id)["segments"][0]
        assert adopted_replacement["asset"]["id"] != adopted_first["asset"]["id"]
        assert saved["template"]["first_frame_asset_id"] == adopted_replacement["asset"]["id"]
        assert adopted_first["asset"]["id"] not in saved["reference_asset_ids"]
        scene = await service.generate(project_id, segment["id"], "scene", [], "An empty street")
        assert scene["filename"] == "seg001_窗邊女子_scene.png"
        adopted_scene = service.adopt(project_id, segment["id"], scene["id"])
        project_after = store.load(project_id)
        assert project_after["segments"][0]["scene_ref_asset_id"] == adopted_scene["asset"]["id"]
        assert "scene" in next(asset for asset in project_after["assets"] if asset["id"] == adopted_scene["asset"]["id"])["roles"]
        assert [c["id"] for c in service.candidates(project_id, segment["id"], "first_frame")] == [first["id"], replacement["id"]]

    asyncio.run(scenario())


def test_image_batch_submits_independent_seeds_before_waiting_and_frees_local_comfy(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Batch stills")
    segment = _blank_segment(1, 15, "segment_001")
    segment["summary"] = "A woman stands by a window"
    segment["shots"] = [{"visual": "Woman beside a window", "start_seconds": 0}]
    project["segments"] = [segment]
    store.save(project)
    events = []
    seeds = iter((101, 101, 202, 303, 404, 505))
    monkeypatch.setattr("app.segment_images.secrets.randbelow", lambda upper: next(seeds))

    class FakeLlama:
        config = SimpleNamespace(llm_provider="local")

        @asynccontextmanager
        async def operation(self, name):
            yield

        async def completion(self, messages, mode="strict"):
            return "窗邊女子"

        async def stop(self):
            events.append("stop_llm")

    class FakeClient:
        prefixes = {}
        workflows = []
        fail_free = False

        def __init__(self, url):
            pass

        async def health(self):
            return {}

        async def submit(self, workflow):
            self.workflows.append(workflow)
            prompt_id = f"prompt-{len(self.workflows)}"
            self.prefixes[prompt_id] = next(node["inputs"]["filename_prefix"] for node in workflow.values()
                                                if node["class_type"] == "SaveImageAdvanced")
            events.append(f"submit:{prompt_id}")
            return prompt_id

        async def wait(self, prompt_id):
            if prompt_id == "prompt-1":
                assert len(self.workflows) == 4
            events.append(f"wait:{prompt_id}")
            return {"prefix": self.prefixes[prompt_id]}

        def outputs(self, history):
            return [ComfyOutput(filename="image_00001.png", subfolder=history["prefix"],
                                output_type="output", media_type="images")]

        async def download(self, output, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 16), "purple").save(destination)
            events.append("download")

        async def free_memory(self):
            events.append("free")
            if self.fail_free:
                raise RuntimeError("ComfyUI free failed")

    monkeypatch.setattr("app.segment_images.ComfyClient", FakeClient)

    async def scenario():
        service = SegmentImageService(store, FakeLlama(), AppConfig())
        result = await service.generate_batch(project["id"], segment["id"], "scene", [], "A window", 4)
        assert len(result["candidates"]) == 4
        assert result["errors"] == []
        assert [item["seed"] for item in result["candidates"]] == [101, 202, 303, 404]
        assert len({item["prompt_id"] for item in result["candidates"]}) == 4
        assert [item["filename"] for item in result["candidates"]] == [
            "seg001_窗邊女子_scene.png", "seg001_窗邊女子_scene(1).png",
            "seg001_窗邊女子_scene(2).png", "seg001_窗邊女子_scene(3).png",
        ]
        assert events.index("submit:prompt-4") < events.index("wait:prompt-1")
        assert events[-1] == "free"
        assert all(node["inputs"]["batch_size"] == 1 for workflow in FakeClient.workflows
                   for node in workflow.values() if node["class_type"] == "EmptyLatentImage")
        assert len(store.load(project["id"])["segments"][0]["image_candidates"]) == 4
        FakeClient.fail_free = True
        another = await service.generate_batch(project["id"], segment["id"], "scene", [], "A window", 1)
        assert len(another["candidates"]) == 1
        assert another["memory_release_error"] == "ComfyUI free failed"

    asyncio.run(scenario())


def test_image_batch_rejects_counts_outside_one_to_four(tmp_path):
    service = SegmentImageService(ProjectStore(tmp_path / "projects"), None, AppConfig())
    for value in (0, 5, 1.5, True):
        with pytest.raises(ValueError, match="1–4"):
            asyncio.run(service.generate_batch("project", "segment", "scene", [], "prompt", value))
