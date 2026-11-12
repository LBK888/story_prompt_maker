import json
from pathlib import Path

import pytest

from app.template_engine import (
    TemplateValidationError,
    load_template,
    render_template,
    validate_template,
)


def test_json_aware_replacement_preserves_types_and_escaping(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps({
        "prompt": "[[PROMPT]]",
        "seed": "[[SEED]]",
        "duration": "[[DURATION]]",
        "output": "[[OUTPUT_PREFIX]]",
    }), encoding="utf-8")
    template = load_template(path)
    rendered = render_template(template, {
        "PROMPT": 'line 1\n"quoted"',
        "SEED": 123,
        "DURATION": 7.5,
        "OUTPUT_PREFIX": "project/segment/run",
    })
    assert rendered["prompt"] == 'line 1\n"quoted"'
    assert rendered["seed"] == 123
    assert isinstance(rendered["seed"], int)
    assert rendered["duration"] == 7.5

    blank_prompt = render_template(template, {
        "PROMPT": "",
        "SEED": 123,
        "DURATION": 7.5,
        "OUTPUT_PREFIX": "project/segment/run",
    })
    assert blank_prompt["prompt"] == ""


def test_placeholder_must_be_entire_value() -> None:
    with pytest.raises(TemplateValidationError, match="entire JSON value"):
        validate_template({"value": "prefix/[[OUTPUT_PREFIX]]"})


def test_repeated_placeholder_is_supported() -> None:
    assert validate_template({"a": "[[OUTPUT_PREFIX]]", "b": "[[OUTPUT_PREFIX]]"}) == ("OUTPUT_PREFIX",)


def test_output_prefix_is_required() -> None:
    with pytest.raises(TemplateValidationError, match="OUTPUT_PREFIX"):
        validate_template({"a": "[[PROMPT]]"})


def test_missing_replacement_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_text('{"a":"[[PROMPT]]","o":"[[OUTPUT_PREFIX]]"}', encoding="utf-8")
    template = load_template(path)
    with pytest.raises(TemplateValidationError, match="Missing replacements"):
        render_template(template, {"PROMPT": "hello"})


def test_sidecar_note_never_enters_workflow_or_replaces_a_note_node(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    workflow = {
        "note": {"class_type": "Note", "inputs": {"text": "ComfyUI node"}},
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": "[[OUTPUT_PREFIX]]"}},
    }
    path.write_text(json.dumps(workflow), encoding="utf-8")
    path.with_suffix(".note.txt").write_text(
        "作者：測試\nserver: 192.0.2.10\n[[NOT_A_WORKFLOW_TOKEN]]\n", encoding="utf-8"
    )

    template = load_template(path)
    assert template.note == "作者：測試\nserver: 192.0.2.10\n[[NOT_A_WORKFLOW_TOKEN]]"
    assert template.content == workflow
    assert render_template(template, {"OUTPUT_PREFIX": "out/file"}) == {
        "note": workflow["note"],
        "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": "out/file"}},
    }

    path.with_suffix(".note.txt").unlink()
    assert load_template(path).note == ""
    assert load_template(path).sha256 != template.sha256

    path.with_suffix(".note.txt").write_bytes(b"\xff")
    assert load_template(path).content == workflow
    assert load_template(path).note == ""
