from __future__ import annotations

from typing import Any


class TakeOutputError(ValueError):
    """Raised when a take output cannot be detached from a project."""


def _find_segment_and_take(
    project: dict[str, Any], segment_id: str, take_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    segment = next(
        (item for item in project.get("segments", []) if item.get("id") == segment_id),
        None,
    )
    if segment is None:
        raise TakeOutputError("Segment not found")
    take = next((item for item in segment.setdefault("takes", []) if item.get("id") == take_id), None)
    if take is None:
        raise TakeOutputError("Take not found")
    return segment, take


def _refresh_segment_status(segment: dict[str, Any]) -> None:
    takes = segment.get("takes", [])
    active_take = next(
        (
            item
            for item in takes
            if item.get("id") == segment.get("active_take_id")
            and item.get("status") == "completed"
            and item.get("local_path")
        ),
        None,
    )
    if active_take is not None:
        segment["status"] = "completed"
        return
    if segment.get("status") in {"preparing", "uploading", "submitted", "running"}:
        return
    has_completed_output = any(
        item.get("status") == "completed" and item.get("outputs") for item in takes
    )
    segment["status"] = "needs_take_selection" if has_completed_output else "planned"


def detach_take_output(
    project: dict[str, Any],
    segment_id: str,
    take_id: str,
    output_index: int,
) -> dict[str, Any]:
    """Remove one output reference from project state without deleting its file."""
    segment, take = _find_segment_and_take(project, segment_id, take_id)
    takes = segment.setdefault("takes", [])
    outputs = take.get("outputs")
    if not isinstance(outputs, list) or not 0 <= output_index < len(outputs):
        raise TakeOutputError("Invalid output index")

    removed_path = outputs.pop(output_index)
    removed_active_output = (
        segment.get("active_take_id") == take_id
        and take.get("local_path") == removed_path
    )
    if removed_active_output:
        segment["active_take_id"] = None
        take["local_path"] = None
        take["actual_duration_seconds"] = None

    removed_take = not outputs
    if removed_take:
        takes.remove(take)

    _refresh_segment_status(segment)

    if removed_active_output:
        for later in project.get("segments", []):
            if (
                int(later.get("index", 0)) > int(segment.get("index", 0))
                and later.get("continuity_from_previous")
            ):
                later["continuity_maybe_stale"] = True

    return {
        "segment": segment,
        "removed_path": removed_path,
        "removed_take": removed_take,
        "file_preserved": True,
    }


def delete_error_record(
    project: dict[str, Any], segment_id: str, take_id: str
) -> dict[str, Any]:
    """Remove a failed take/error record; no referenced media file is deleted."""
    segment, take = _find_segment_and_take(project, segment_id, take_id)
    if not str(take.get("error") or "").strip():
        raise TakeOutputError("Take has no error record")
    segment["takes"].remove(take)
    if segment.get("active_take_id") == take_id:
        segment["active_take_id"] = None
    _refresh_segment_status(segment)
    return {
        "segment": segment,
        "deleted_take_id": take_id,
        "record_preserved": False,
    }
