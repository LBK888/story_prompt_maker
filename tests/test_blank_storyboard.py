import asyncio
import math

import pytest
from fastapi import HTTPException

import app.main as main_module
from app.blank_storyboard import (
    ExistingStoryboardError,
    add_blank_segment,
    create_blank_storyboard,
    delete_segment,
)
from app.domain import new_project
from app.storage import ProjectStore


@pytest.mark.parametrize("minimum", [4, 15, 16, 30, 31, 600])
def test_blank_storyboard_count_is_ceiling_of_minimum_over_fifteen(minimum: int) -> None:
    project = new_project("Blank")
    project["settings"]["minimum_duration_seconds"] = minimum

    result = create_blank_storyboard(project)

    assert len(result["segments"]) == math.ceil(minimum / 15)
    assert sum(segment["duration_seconds"] for segment in result["segments"]) >= minimum
    assert all(4 <= segment["duration_seconds"] <= 15 for segment in result["segments"])


def test_blank_storyboard_has_editable_empty_content_and_complete_state() -> None:
    project = new_project("Blank")
    project["settings"]["minimum_duration_seconds"] = 16

    result = create_blank_storyboard(project)

    assert [segment["duration_seconds"] for segment in result["segments"]] == [8, 8]
    assert result["blueprint"]["creation_mode"] == "manual_blank"
    assert result["blueprint"]["planned_duration_seconds"] == 16
    for index, segment in enumerate(result["segments"], start=1):
        assert segment["id"] == f"segment_{index:03d}"
        assert segment["summary"] == ""
        assert segment["shots"] == []
        assert segment["dialogue"] == []
        assert segment["prompt"] == ""
        assert segment["prompt_status"] == "missing"
        assert segment["prompt_source"] == "llm"
        assert segment["takes"] == []


def test_blank_prompt_stays_missing_after_project_reload(tmp_path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Blank")
    create_blank_storyboard(project)
    store.save(project)

    segment = store.load(project["id"])["segments"][0]

    assert segment["prompt"] == ""
    assert segment["summary"] == ""
    assert segment["prompt_status"] == "missing"
    assert segment["prompt_source"] == "llm"


def test_blank_storyboard_never_replaces_existing_segments() -> None:
    project = new_project("Protected")
    existing = {"id": "segment_existing", "prompt": "keep me"}
    project["segments"] = [existing]

    with pytest.raises(ExistingStoryboardError):
        create_blank_storyboard(project)

    assert project["segments"] == [existing]


def test_blank_blueprint_endpoint_persists_and_rejects_a_second_creation(tmp_path, monkeypatch) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Blank")
    project["settings"]["minimum_duration_seconds"] = 31
    store.save(project)
    monkeypatch.setattr(main_module, "store", store)

    created = asyncio.run(main_module.generate_blank_blueprint(project["id"]))

    assert len(created["segments"]) == 3
    assert len(store.load(project["id"])["segments"]) == 3
    with pytest.raises(HTTPException) as error:
        asyncio.run(main_module.generate_blank_blueprint(project["id"]))
    assert error.value.status_code == 409
    assert len(store.load(project["id"])["segments"]) == 3


def test_add_blank_segment_inserts_after_target_without_changing_existing_ids() -> None:
    project = new_project("Structure")
    project["settings"]["minimum_duration_seconds"] = 30
    create_blank_storyboard(project)
    original_ids = [segment["id"] for segment in project["segments"]]

    added = add_blank_segment(project, original_ids[0])

    assert [segment["id"] for segment in project["segments"] if segment is not added] == original_ids
    assert project["segments"][1] is added
    assert added["duration_seconds"] == 15
    assert added["summary"] == ""
    assert added["shots"] == []
    assert added["prompt_status"] == "missing"
    assert [segment["index"] for segment in project["segments"]] == [1, 2, 3]
    assert [segment["title"] for segment in project["segments"]] == ["片段 01", "片段 02", "片段 03"]
    assert project["blueprint"]["planned_duration_seconds"] == 45


def test_delete_segment_reindexes_records_but_preserves_generated_file(tmp_path) -> None:
    video = tmp_path / "output.mp4"
    video.write_bytes(b"video")
    project = new_project("Structure")
    project["settings"]["minimum_duration_seconds"] = 30
    create_blank_storyboard(project)
    first_id, second_id = [segment["id"] for segment in project["segments"]]
    project["segments"][0]["takes"] = [{"id": "take_1", "outputs": [str(video)]}]

    removed = delete_segment(project, first_id)

    assert removed["id"] == first_id
    assert [segment["id"] for segment in project["segments"]] == [second_id]
    assert project["segments"][0]["index"] == 1
    assert project["segments"][0]["title"] == "片段 01"
    assert project["blueprint"]["planned_duration_seconds"] == 15
    assert video.exists()


def test_delete_last_segment_marks_blueprint_missing() -> None:
    project = new_project("Structure")
    project["settings"]["minimum_duration_seconds"] = 15
    create_blank_storyboard(project)

    delete_segment(project, project["segments"][0]["id"])

    assert project["segments"] == []
    assert project["blueprint_status"] == "missing"
    assert project["blueprint"]["planned_duration_seconds"] == 0
