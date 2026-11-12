from app.blank_storyboard import create_blank_storyboard, add_blank_segment
from app.domain import new_project
from app.prompt_policy import blueprint_schema, blueprint_messages


def test_preferred_segment_length_controls_blank_and_llm_storyboards():
    project = new_project("Duration")
    project["settings"]["minimum_duration_seconds"] = 50
    project["settings"]["segment_duration_seconds"] = 25
    messages = blueprint_messages(project)
    assert "Prefer 25 seconds" in messages[0]["content"]
    assert blueprint_schema()["properties"]["segments"]["items"]["properties"]["duration_seconds"]["maximum"] == 25
    create_blank_storyboard(project)
    assert [segment["duration_seconds"] for segment in project["segments"]] == [25, 25]
    assert add_blank_segment(project)["duration_seconds"] == 25
