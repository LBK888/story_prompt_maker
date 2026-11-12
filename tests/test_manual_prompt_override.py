import asyncio
from pathlib import Path

import pytest

import app.main as main_module
from app.readiness import generation_readiness
from app.storage import ProjectStore


@pytest.mark.parametrize(
    "manual_prompt",
    [
        "",
        "User-authored prompt that intentionally omits every automatic validation requirement.",
        "  使用者原文保留繁體與空白  \n",
    ],
)
def test_manual_prompt_is_an_unvalidated_generation_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manual_prompt: str,
) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Manual override", "Story")
    project["segments"] = [
        {
            "id": "segment_001",
            "index": 1,
            "title": "Manual segment",
            "task_type": "T2VA",
            "duration_seconds": 8,
            "shots": [{"start_seconds": 0, "visual": "A room", "framing": "wide"}],
            "dialogue": [
                {
                    "kind": "dialogue",
                    "speaker_id": "speaker",
                    "spoken_text": "Locked spoken text",
                    "language": "English",
                }
            ],
            "prompt": "",
            "prompt_status": "missing",
            "prompt_source": "llm",
            "template": {"name": "workflow", "tokens": [], "image_order": []},
        }
    ]
    store.save(project)
    monkeypatch.setattr(main_module, "store", store)

    saved = asyncio.run(
        main_module.save_manual_prompt(project["id"], "segment_001", {"prompt": manual_prompt})
    )

    assert saved["prompt"] == manual_prompt
    assert saved["prompt_source"] == "manual"
    assert saved["prompt_status"] == "ready"
    assert saved["prompt_validation_errors"] == []
    assert saved["prompt_validation_warnings"] == []
    assert generation_readiness([saved]) == []

    reloaded = store.load(project["id"])["segments"][0]
    assert reloaded["prompt"] == manual_prompt
    assert reloaded["prompt_source"] == "manual"
    assert reloaded["prompt_status"] == "ready"
    assert reloaded["prompt_validation_errors"] == []
    assert reloaded["prompt_validation_warnings"] == []
    assert generation_readiness([reloaded]) == []
