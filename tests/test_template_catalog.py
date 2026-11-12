from pathlib import Path
import json

from app.blank_storyboard import create_blank_storyboard
from app.storage import ProjectStore
from app.template_catalog import TemplateCatalog
from app.template_engine import WorkflowTemplate


def test_template_and_h3_task_are_persisted_together(tmp_path, monkeypatch) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Template task")
    project["settings"]["minimum_duration_seconds"] = 15
    create_blank_storyboard(project)
    store.save(project)
    catalog = TemplateCatalog(store)
    workflow = WorkflowTemplate(
        name="workflow.json",
        path=Path("workflow.json"),
        sha256="a" * 64,
        content={"1": {"inputs": {"prompt": "[[PROMPT]]"}}},
        tokens=("PROMPT",),
    )
    monkeypatch.setattr(catalog, "get", lambda _: workflow)

    segment = catalog.assign(project["id"], "segment_001", "workflow.json", "I2VA")

    reloaded = store.load(project["id"])["segments"][0]
    assert segment["task_type"] == "I2VA"
    assert segment["template"]["name"] == "workflow.json"
    assert reloaded["task_type"] == "I2VA"
    assert reloaded["template"]["name"] == "workflow.json"
    assert reloaded["prompt_status"] == "stale"


def test_assignment_snapshots_note_separately_from_workflow(tmp_path, monkeypatch) -> None:
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Template note")
    create_blank_storyboard(project)
    store.save(project)
    source = tmp_path / "workflow.json"
    workflow = {"note": {"class_type": "Note", "inputs": {"text": "node"}},
                "1": {"inputs": {"filename_prefix": "[[OUTPUT_PREFIX]]"}}}
    source.write_text(json.dumps(workflow), encoding="utf-8")
    source.with_suffix(".note.txt").write_text("作者與版本 v1\n", encoding="utf-8")
    catalog = TemplateCatalog(store)
    from app.template_engine import load_template
    monkeypatch.setattr(catalog, "get", lambda _: load_template(source))

    segment = catalog.assign(project["id"], "segment_001", "workflow")
    snapshot = catalog.load_snapshot(project["id"], segment)
    assert segment["template"]["note"] == "作者與版本 v1"
    assert snapshot.note == "作者與版本 v1"
    assert snapshot.content == workflow
