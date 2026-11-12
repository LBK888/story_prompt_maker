from __future__ import annotations

from typing import Any

from .domain import new_id
from .timing import partition_duration


class ExistingStoryboardError(ValueError):
    """Raised when blank storyboard creation would overwrite existing segments."""


class StoryboardStructureError(ValueError):
    """Raised when a segment structure mutation cannot be applied safely."""


def _blank_segment(index: int, duration: int, segment_id: str) -> dict[str, Any]:
    return {
        "id": segment_id,
        "index": index,
        "title": f"片段 {index:02d}",
        "summary": "",
        "duration_seconds": duration,
        "task_type": "T2VA",
        "shots": [],
        "dialogue": [],
        "dialogue_estimated_seconds": 0,
        "continuity_from_previous": False,
        "reference_asset_ids": [],
        "prompt": "",
        "prompt_status": "missing",
        "prompt_source": "llm",
        "prompt_validation_errors": [],
        "prompt_validation_warnings": [],
        "template": None,
        "takes": [],
        "active_take_id": None,
        "status": "planned",
        "creation_mode": "manual_blank",
    }


def _refresh_structure(project: dict[str, Any], changed_index: int) -> None:
    segments = project.get("segments", [])
    for index, segment in enumerate(segments, start=1):
        previous_index = int(segment.get("index", index))
        if segment.get("title") == f"片段 {previous_index:02d}":
            segment["title"] = f"片段 {index:02d}"
        segment["index"] = index
        if index == 1:
            segment["continuity_from_previous"] = False
        elif index >= changed_index and segment.get("continuity_from_previous"):
            segment["continuity_maybe_stale"] = True

    blueprint = project.setdefault("blueprint", {}) or {}
    project["blueprint"] = blueprint
    blueprint["planned_duration_seconds"] = sum(
        int(segment.get("duration_seconds", 0)) for segment in segments
    )
    blueprint["timing_conflicts"] = []
    project["blueprint_status"] = "ready" if segments else "missing"


def create_blank_storyboard(project: dict[str, Any]) -> dict[str, Any]:
    """Create editable blank segments without invoking an LLM or replacing existing data."""
    if project.get("segments"):
        raise ExistingStoryboardError("A storyboard already exists; existing data was preserved")

    minimum_seconds = int(project["settings"]["minimum_duration_seconds"])
    durations = partition_duration(minimum_seconds, int(project["settings"].get("segment_duration_seconds", 15)))
    segments: list[dict[str, Any]] = []
    for index, duration in enumerate(durations, start=1):
        segments.append(_blank_segment(index, duration, f"segment_{index:03d}"))

    project["blueprint"] = {
        "creation_mode": "manual_blank",
        "planned_duration_seconds": sum(durations),
        "timing_conflicts": [],
        "continuity_bible": {},
        "sound_bible": {},
    }
    project["segments"] = segments
    project["blueprint_status"] = "ready"
    return project


def add_blank_segment(project: dict[str, Any], after_segment_id: str | None = None) -> dict[str, Any]:
    """Insert a blank segment using the project's preferred duration."""
    segments = project.setdefault("segments", [])
    if after_segment_id is None:
        insert_at = len(segments)
    else:
        insert_at = next(
            (index + 1 for index, item in enumerate(segments) if item.get("id") == after_segment_id),
            -1,
        )
        if insert_at < 0:
            raise StoryboardStructureError("Segment not found")

    segment = _blank_segment(insert_at + 1, int(project["settings"].get("segment_duration_seconds", 15)), new_id("segment"))
    segments.insert(insert_at, segment)
    _refresh_structure(project, insert_at + 1)
    return segment


def delete_segment(project: dict[str, Any], segment_id: str) -> dict[str, Any]:
    """Permanently remove one segment record without deleting generated media files."""
    segments = project.get("segments", [])
    remove_at = next(
        (index for index, item in enumerate(segments) if item.get("id") == segment_id),
        -1,
    )
    if remove_at < 0:
        raise StoryboardStructureError("Segment not found")
    segment = segments.pop(remove_at)
    _refresh_structure(project, remove_at + 1)
    return segment
