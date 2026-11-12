from app.readiness import active_video_takes, generation_readiness


def segment(prompt_status="ready", prompt="prompt", template=True):
    return {
        "id": "segment_001",
        "prompt_status": prompt_status,
        "prompt": prompt,
        "template": {"name": "workflow"} if template else None,
    }


def test_generation_readiness_explains_missing_prompt_and_template() -> None:
    errors = generation_readiness([segment(prompt_status="missing", prompt="", template=False)])
    assert any("prompt" in error for error in errors)
    assert any("workflow template" in error for error in errors)


def test_generation_readiness_accepts_ready_prompt_and_template() -> None:
    assert generation_readiness([segment()]) == []


def test_generation_readiness_requires_every_ref_img_slot() -> None:
    item = segment()
    item["template"] = {"name": "workflow", "tokens": ["PROMPT", "REF_IMG1", "REF_IMG2"], "image_order": ["a"]}
    errors = generation_readiness([item])
    assert any("REF" in error for error in errors)

    item["template"]["image_order"] = ["a", "b"]
    assert generation_readiness([item]) == []


def test_active_video_takes_returns_only_completed_local_outputs() -> None:
    segments = [
        {"active_take_id": "a", "takes": [{"id": "a", "status": "completed", "local_path": "a.mp4"}]},
        {"active_take_id": "b", "takes": [{"id": "b", "status": "failed", "local_path": "b.mp4"}]},
        {"active_take_id": None, "takes": []},
    ]
    assert [take["id"] for _, take in active_video_takes(segments)] == ["a"]
