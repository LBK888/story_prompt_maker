import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from app.planner import StoryPlanner
from app.prompt_templates import load_prompt_templates


def test_prompt_templates_collect_all_json_files_and_entries(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"templates": [
        {"Name": "A", "Type": "整段", "Note": "", "Prompt": "Whole prompt"},
        {"Name": "B", "Type": "片段", "Note": "插入到前面", "Prompt": "Part prompt"},
    ]}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([
        {"Name": "C", "Type": "片段", "Note": "", "Prompt": "Another part"},
    ]), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    result = load_prompt_templates(tmp_path)
    assert [item["Name"] for item in result["templates"]] == ["A", "B", "C"]
    assert result["errors"][0].startswith("broken.json:")


@pytest.mark.parametrize("task_type,current,improved", [
    (
        "T2VA",
        "integrated_multimodal_description: [Shot 1] A person stands.",
        "integrated_multimodal_description: [Shot 1] A person stands.\n\noverall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A",
    ),
    (
        "Ref2VA",
        "subject_definitions: <Subject 1>: the person shown in <Picture 1>.\n<Picture 1>: person reference.",
        "subject_definitions: <Subject 1>: the person shown in <Picture 1>.\n<Picture 1>: person reference.\n\nsummary: A person stands.\n\nretention_analysis: Preserve the person.\n\ndetailed_description: [Shot 1] A person stands.\n\noverall_soundscape: Quiet room tone.\n\nnon_diegetic_music: N/A",
    ),
])
def test_optimize_sends_current_prompt_and_references_without_storyboard(monkeypatch, task_type, current, improved):
    old_dialogue = "哼，死变态，别跟过来!"
    project = {
        "settings": {"story": "STORYBOARD_SECRET", "aspect_ratio": "16:9"},
        "assets": [{"id": "asset-1", "original_name": "portrait.png", "filename": "portrait.png",
                    "media_type": "image", "roles": ["character"], "notes": "Blue jacket"}],
        "segments": [{
            "id": "seg-1", "task_type": task_type, "duration_seconds": 8,
            "summary": "STORYBOARD_SECRET", "shots": [{"visual": "STORYBOARD_SECRET"}],
            "dialogue": [{"kind": "dialogue", "spoken_text": old_dialogue}],
            "template": {"tokens": ["REF_IMG1"], "image_order": []},
        }],
    }

    class Store:
        def load(self, _project_id):
            return project

        def save(self, _project):
            pass

    class Llama:
        messages = None
        calls = 0

        @asynccontextmanager
        async def operation(self, _label):
            yield

        def update_progress(self, *_args):
            pass

        async def completion(self, messages, mode):
            self.messages = messages
            self.calls += 1
            return improved

    llama = Llama()
    monkeypatch.setattr(StoryPlanner, "_h3_asset_images", lambda *_args: [])
    result = asyncio.run(StoryPlanner(Store(), llama).optimize_segment_prompt(
        "project", "seg-1", current, {"image_order": ["asset-1"]}
    ))
    submitted = json.dumps(llama.messages, ensure_ascii=False)
    assert "STORYBOARD_SECRET" not in submitted
    assert old_dialogue not in submitted
    assert json.loads(llama.messages[1]["content"])["current_prompt"] == current
    assert "portrait.png" in submitted
    assert "Output contract" in submitted
    assert f"Task is {task_type}" in submitted
    assert result["prompt"] == improved
    assert result["prompt_status"] == "ready"
    assert result["prompt_validation_errors"] == []
    assert llama.calls == 1
