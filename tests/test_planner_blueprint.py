import asyncio
from pathlib import Path

import pytest

from app.config import AppConfig
from app.llama_service import LlamaServerManager
from app.planner import PlanningError, StoryPlanner
from app.storage import ProjectStore


def empty_blueprint() -> dict:
    return {
        "title": "",
        "story_summary": "",
        "continuity_bible": {"characters": [], "locations": [], "global_visual_rules": []},
        "sound_bible": {"ambience": "", "music": "", "continuity": ""},
        "segments": [
            {
                "title": "片段 01",
                "summary": "片段 01",
                "duration_seconds": 8,
                "task_type": "T2VA",
                "shots": [
                    {
                        "start_seconds": 0,
                        "framing": "medium shot",
                        "visual": "",
                        "action": "",
                        "camera": "medium shot",
                        "audio": "",
                        "speaker_ids": [],
                    }
                ],
                "dialogue": [],
                "continuity_from_previous": False,
                "reference_asset_ids": [],
            }
        ],
    }


def valid_blueprint() -> dict:
    value = empty_blueprint()
    value["title"] = "課後提問"
    value["story_summary"] = "學生在課後向教授提問。"
    value["segments"][0]["title"] = "走向講台"
    value["segments"][0]["summary"] = "學生收拾筆記後走向講台。"
    value["segments"][0]["shots"][0]["visual"] = "學生從座位起身，拿著筆記走向講台。"
    return value


def make_planner(tmp_path: Path) -> tuple[StoryPlanner, ProjectStore, dict]:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Test", "學生在課後向教授提問。")
    project["settings"]["minimum_duration_seconds"] = 8
    project["segments"] = [{"id": "old_segment", "summary": "keep me", "shots": []}]
    store.save(project)
    manager = LlamaServerManager(AppConfig())
    return StoryPlanner(store, manager), store, project


def test_invalid_blueprint_is_retried_and_never_replaces_existing_storyboard(tmp_path: Path) -> None:
    planner, store, project = make_planner(tmp_path)
    calls = 0

    async def fake_json_completion(*args, **kwargs):
        nonlocal calls
        calls += 1
        return empty_blueprint()

    planner.llama.json_completion = fake_json_completion

    with pytest.raises(PlanningError, match="previous storyboard was preserved"):
        asyncio.run(planner.generate_blueprint(project["id"]))

    assert calls == 3
    assert store.load(project["id"])["segments"][0]["id"] == "old_segment"


def test_blueprint_retry_accepts_later_contentful_result(tmp_path: Path) -> None:
    planner, store, project = make_planner(tmp_path)
    responses = iter([empty_blueprint(), valid_blueprint()])

    async def fake_json_completion(*args, **kwargs):
        return next(responses)

    planner.llama.json_completion = fake_json_completion
    result = asyncio.run(planner.generate_blueprint(project["id"]))

    assert result["segments"][0]["summary"] == "學生收拾筆記後走向講台。"
    assert result["segments"][0]["shots"][0]["visual"]


def test_dialogue_checkbox_is_enforced_in_the_llm_json_schema(tmp_path: Path) -> None:
    planner, store, project = make_planner(tmp_path)
    project["settings"]["create_dialogue"] = True
    project["settings"]["dialogue_language"] = "Chinese"
    store.save(project)
    observed_min_items: list[int] = []

    async def fake_json_completion(messages, schema, **kwargs):
        dialogue_schema = schema["properties"]["segments"]["items"]["properties"]["dialogue"]
        observed_min_items.append(int(dialogue_schema.get("minItems", 0)))
        response = valid_blueprint()
        if dialogue_schema.get("minItems", 0) > 0:
            response["segments"][0]["dialogue"] = [
                {
                    "speaker_id": "student",
                    "kind": "dialogue",
                    "language": "Chinese",
                    "original_text": "教授，我想請教這一題。",
                    "spoken_text": "教授，我想请教这一题。",
                    "estimated_seconds": 2.5,
                }
            ]
        return response

    planner.llama.json_completion = fake_json_completion
    result = asyncio.run(planner.generate_blueprint(project["id"]))

    assert observed_min_items == [1]
    assert result["segments"][0]["dialogue"][0]["kind"] == "dialogue"


def test_h3_images_follow_template_picture_order_and_hide_asset_ids(tmp_path: Path, monkeypatch) -> None:
    planner, _, project = make_planner(tmp_path)
    project["assets"] = [
        {"id": "asset_a", "filename": "a.png"},
        {"id": "asset_b", "filename": "b.png"},
        {"id": "asset_first", "filename": "first.png"},
        {"id": "asset_last", "filename": "last.png"},
    ]
    segment = {
        "template": {
            "image_order": ["asset_b", "asset_a"],
            "first_frame_asset_id": "asset_first",
            "last_frame_asset_id": "asset_last",
        }
    }
    monkeypatch.setattr(
        "app.planner.ensure_analysis_copy",
        lambda store, project_id, asset: Path(str(asset["id"])),
    )

    labelled = planner._h3_asset_images(project["id"], project, segment)

    assert [label for label, _ in labelled] == ["Picture 1", "Picture 2", "First frame", "Last frame"]
    assert [path.name for _, path in labelled] == ["asset_b", "asset_a", "asset_first", "asset_last"]


def test_h3_image_attachments_skip_video_and_audio_assets(tmp_path: Path, monkeypatch) -> None:
    planner, _, project = make_planner(tmp_path)
    project["assets"] = [
        {"id": "asset_video", "filename": "clip.mp4", "media_type": "video"},
        {"id": "asset_image", "filename": "portrait.png", "media_type": "image"},
        {"id": "asset_audio", "filename": "voice.wav", "media_type": "audio"},
    ]
    segment = {
        "template": {
            "image_order": ["asset_video", "asset_image", "asset_audio"],
            "first_frame_asset_id": None,
            "last_frame_asset_id": None,
        }
    }
    monkeypatch.setattr(
        "app.planner.ensure_analysis_copy",
        lambda store, project_id, asset: Path(str(asset["id"])),
    )

    labelled = planner._h3_asset_images(project["id"], project, segment)

    assert labelled == [("Picture 2", Path("asset_image"))]
