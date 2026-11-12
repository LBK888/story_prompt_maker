from __future__ import annotations

from typing import Any

from .reference_order import reference_slots


def prompt_is_ready(segment: dict[str, Any]) -> bool:
    if segment.get("prompt_status") != "ready":
        return False
    return segment.get("prompt_source") == "manual" or bool(str(segment.get("prompt") or "").strip())


def generation_readiness(segments: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not segments:
        return ["尚未建立影片藍圖"]
    for segment in segments:
        label = str(segment.get("title") or segment.get("id") or "未命名片段")
        if not prompt_is_ready(segment):
            errors.append(f"{label} 尚未有可用的 prompt")
        template = segment.get("template")
        if not template:
            errors.append(f"{label} 尚未套用 workflow template JSON")
        else:
            slots = reference_slots(template.get('tokens', []))
            mapping = template.get('reference_mapping')
            ready = all(mapping.get(s) for s in slots) if mapping is not None else (
                len(template.get('image_order', [])) == len(slots) and all(template.get('image_order', [])))
            if not ready:
                errors.append(f"{label} 的 REF 參考媒體插槽尚未完整設定")
    return errors


def active_video_takes(
    segments: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for segment in sorted(segments, key=lambda item: int(item.get("index", 0))):
        take = next(
            (
                item
                for item in segment.get("takes", [])
                if item.get("id") == segment.get("active_take_id")
                and item.get("status") == "completed"
                and item.get("local_path")
            ),
            None,
        )
        if take:
            active.append((segment, take))
    return active
