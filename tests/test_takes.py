from app.takes import TakeOutputError, delete_error_record, detach_take_output
from app.main import app


def project_with_outputs() -> dict:
    return {
        "segments": [
            {
                "id": "segment_001",
                "index": 1,
                "status": "completed",
                "active_take_id": "take_1",
                "takes": [
                    {
                        "id": "take_1",
                        "status": "completed",
                        "outputs": ["one.mp4", "two.mp4"],
                        "local_path": "one.mp4",
                        "actual_duration_seconds": 8.0,
                    }
                ],
            },
            {
                "id": "segment_002",
                "index": 2,
                "continuity_from_previous": True,
                "takes": [],
            },
        ]
    }


def test_detach_active_output_preserves_file_and_requires_new_selection(tmp_path) -> None:
    video = tmp_path / "one.mp4"
    video.write_bytes(b"video")
    project = project_with_outputs()

    result = detach_take_output(project, "segment_001", "take_1", 0)

    segment = result["segment"]
    assert result == {
        "segment": segment,
        "removed_path": "one.mp4",
        "removed_take": False,
        "file_preserved": True,
    }
    assert video.exists()
    assert segment["takes"][0]["outputs"] == ["two.mp4"]
    assert segment["active_take_id"] is None
    assert segment["status"] == "needs_take_selection"
    assert project["segments"][1]["continuity_maybe_stale"] is True


def test_detach_only_output_removes_empty_take_but_not_file(tmp_path) -> None:
    video = tmp_path / "one.mp4"
    video.write_bytes(b"video")
    project = project_with_outputs()
    project["segments"][0]["takes"][0]["outputs"] = ["one.mp4"]

    result = detach_take_output(project, "segment_001", "take_1", 0)

    assert result["removed_take"] is True
    assert result["segment"]["takes"] == []
    assert result["segment"]["status"] == "planned"
    assert video.exists()


def test_detach_rejects_unknown_output() -> None:
    project = project_with_outputs()

    try:
        detach_take_output(project, "segment_001", "take_1", 9)
    except TakeOutputError as exc:
        assert str(exc) == "Invalid output index"
    else:
        raise AssertionError("Expected TakeOutputError")


def test_detach_output_api_is_registered() -> None:
    path = "/api/projects/{project_id}/segments/{segment_id}/takes/{take_id}/outputs/{output_index}"
    assert "delete" in app.openapi()["paths"][path]


def test_delete_error_record_removes_failed_take_and_restores_segment_status() -> None:
    project = project_with_outputs()
    segment = project["segments"][0]
    segment["status"] = "failed"
    segment["takes"].append(
        {"id": "take_failed", "status": "failed", "outputs": [], "error": "line 1\nline 2"}
    )

    result = delete_error_record(project, "segment_001", "take_failed")

    assert result["record_preserved"] is False
    assert [take["id"] for take in segment["takes"]] == ["take_1"]
    assert segment["status"] == "completed"


def test_delete_error_record_rejects_a_take_without_an_error() -> None:
    project = project_with_outputs()

    try:
        delete_error_record(project, "segment_001", "take_1")
    except TakeOutputError as exc:
        assert str(exc) == "Take has no error record"
    else:
        raise AssertionError("Expected TakeOutputError")


def test_delete_error_api_is_registered() -> None:
    path = "/api/projects/{project_id}/segments/{segment_id}/takes/{take_id}/error"
    assert "delete" in app.openapi()["paths"][path]
