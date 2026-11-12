from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


PROJECT_SCHEMA_VERSION = 1
ASPECT_RATIOS = {
    "16:9": (16, 9),
    "9:16": (9, 16),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "21:9": (21, 9),
}
STYLE_CATEGORIES = {
    "live_action",
    "2d_animation",
    "3d_animation",
    "stop_motion",
    "live_handdrawn_mix",
    "live_3d_mix",
    "custom",
}
EXPANSION_MODES = {"strict", "balanced", "creative"}
REFERENCE_ROLES = {
    "first_frame",
    "last_frame",
    "character",
    "scene",
    "object",
    "style",
    "composition",
    "action",
}
TASK_TYPES = {"T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def new_project(name: str, story: str = "") -> dict[str, Any]:
    now = utc_now()
    project_id = new_id("project")
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "id": project_id,
        "name": name.strip() or "Untitled project",
        "created_at": now,
        "updated_at": now,
        "archived": False,
        "settings": {
            "story": story,
            "minimum_duration_seconds": 30,
            "segment_duration_seconds": 15,
            "duration_tolerance_ratio": 0.10,
            "aspect_ratio": "16:9",
            "target_megapixels": 1.0,
            "style_category": "live_action",
            "style_notes": "",
            "expansion_mode": "balanced",
            "official_preset": "AUTO",
            "create_dialogue": False,
            "dialogue_language": "Chinese",
            "create_narration": False,
            "narration_language": "Chinese",
            "speech_speed": "normal",
        },
        "assets": [],
        "blueprint": None,
        "segments": [],
        "final_outputs": [],
        "events": [],
    }


def new_event(kind: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "id": new_id("event"),
        "time": utc_now(),
        "kind": kind,
        "message": message,
        "details": details,
    }
