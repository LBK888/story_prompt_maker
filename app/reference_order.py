from __future__ import annotations

import json
import re
from typing import Any, Iterable
from .character_assets import asset_kind


REF_IMAGE_TOKEN = re.compile(r"REF_(IMG|AUD|VID)(0|[1-9][0-9]*)")


def reference_slots(tokens: Iterable[str]) -> list[str]:
    numbered = {str(token) for token in tokens if REF_IMAGE_TOKEN.fullmatch(str(token))}
    result = sorted(numbered, key=lambda token: (int(REF_IMAGE_TOKEN.fullmatch(token).group(2)), token))
    return result + (['Drive_AUD'] if 'Drive_AUD' in tokens else [])


def resolve_references(binding, assets, *, require_complete=False):
    """Allocate selected media in order to compatible tokens. Characters fill paired indices."""
    slots = reference_slots(binding.get('tokens', []))
    assets = {a['id']: a for a in assets}
    mapping = {}
    prefixes = {'image': 'REF_IMG', 'audio': 'REF_AUD', 'video': 'REF_VID'}
    for asset_id in binding.get('image_order', []):
        if not asset_id:
            continue
        if asset_id not in assets:
            raise ValueError(f'找不到素材：{asset_id}')
        kind = asset_kind(assets[asset_id])
        kinds = ['image', 'audio'] if kind == 'character' else [kind]
        used = False
        if 'audio' in kinds and 'Drive_AUD' in slots and 'Drive_AUD' not in mapping:
            mapping['Drive_AUD'] = asset_id
            used = True
        # Prefer an available image/audio pair with the same index for a character.
        paired = next((s for s in slots if s.startswith('REF_IMG') and s not in mapping
                       and s.replace('REF_IMG', 'REF_AUD') in slots
                       and s.replace('REF_IMG', 'REF_AUD') not in mapping), None) if kind == 'character' else None
        if paired:
            mapping[paired] = mapping[paired.replace('REF_IMG', 'REF_AUD')] = asset_id
            used = True
        else:
            for media in kinds:
                token = next((s for s in slots if s.startswith(prefixes[media]) and s not in mapping), None)
                if token:
                    mapping[token] = asset_id
                    used = True
        if not used:
            raise ValueError(f"素材 {assets[asset_id].get('original_name', asset_id)} 沒有可用的相容插槽")
    missing = [s for s in slots if s not in mapping]
    if require_complete and missing:
        raise ValueError('尚未設定：' + '、'.join(f'[[{s}]]' for s in missing))
    return mapping


def bind_references(binding, assets, *, require_complete=False):
    mapping = resolve_references(binding, assets, require_complete=require_complete)
    binding['reference_mapping'] = mapping
    return mapping


def reference_candidates(project: dict[str, Any], segment: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the project assets available to the explicit template-slot picker."""
    return list(project.get("assets", []))


def bound_reference_asset_ids(segment: dict[str, Any]) -> list[str]:
    """Derive the LLM reference set solely from the segment's template mapping."""
    binding = segment.get("template") or {}
    candidates = [
        binding.get("first_frame_asset_id") or segment.get("generated_image_assets", {}).get("first_frame"),
        *binding.get("image_order", []),
        binding.get("last_frame_asset_id") or segment.get("generated_image_assets", {}).get("last_frame"),
        segment.get("scene_ref_asset_id"),
    ]
    return list(dict.fromkeys(str(asset_id) for asset_id in candidates if asset_id))


def picture_asset_ids(segment: dict[str, Any]) -> list[str]:
    """Return REF_IMG assets in the exact ComfyUI input order, without deduplication."""
    binding = segment.get("template") or {}
    if 'reference_mapping' in binding:
        return [binding['reference_mapping'][s] for s in reference_slots(binding.get('tokens', []))
                if s.startswith('REF_IMG') and s in binding['reference_mapping']]
    return [str(asset_id) for asset_id in binding.get("image_order", []) if asset_id]


def picture_slots(segment: dict[str, Any]) -> list[str]:
    binding = segment.get("template") or {}
    slots = [s for s in reference_slots(binding.get("tokens", [])) if s.startswith('REF_IMG')]
    count = len(picture_asset_ids(segment))
    return [slots[index] if index < len(slots) else f"REF_IMG{index + 1}" for index in range(count)]


def picture_reference_context(project: dict[str, Any], segment: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose positional Picture aliases to the prompt model without leaking internal IDs."""
    assets = {str(asset.get("id")): asset for asset in project.get("assets", [])}
    result: list[dict[str, Any]] = []
    slots = picture_slots(segment)
    for index, asset_id in enumerate(picture_asset_ids(segment), start=1):
        asset = assets.get(asset_id)
        if asset is None:
            continue
        result.append(
            {
                "picture": f"<Picture {index}>",
                "picture_index": index,
                "template_slot": slots[index - 1],
                "roles": asset.get("roles", []),
                "notes": asset.get("notes", ""),
                "character_name": asset.get("character_name", ""),
                "original_name": asset.get("original_name", ""),
                "media_type": asset.get("media_type", "image"),
            }
        )
    return result


def media_reference_context(project, segment):
    binding = segment.get('template') or {}
    assets = {a['id']: a for a in project.get('assets', [])}
    mapping = binding.get('reference_mapping', {})
    result = []
    for token in reference_slots(binding.get('tokens', [])):
        if token.startswith('REF_IMG') or token not in mapping:
            continue
        asset = assets.get(mapping[token])
        if asset:
            result.append({'template_slot': token, 'media_type': 'video' if token.startswith('REF_VID') else 'audio',
                           'character_name': asset.get('character_name', ''), 'notes': asset.get('notes', ''),
                           'sample_line': asset.get('voice_line', ''), 'original_name': asset.get('original_name', '')})
    return result


def reference_order_schema(asset_ids: list[str], slot_count: int, flexible: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["asset_ids", "rationale"],
        "properties": {
            "asset_ids": {
                "type": "array",
                "items": {"type": "string", "enum": asset_ids},
                "minItems": 1 if flexible else slot_count,
                "maxItems": slot_count,
                "uniqueItems": True,
            },
            "rationale": {"type": "string"},
        },
    }


def reference_order_messages(
    project: dict[str, Any],
    segment: dict[str, Any],
    slots: list[str],
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    system = """You assign user-provided reference media files to numbered MiniMax H3 workflow slots.
Return only JSON matching the supplied schema.

Rules:
- Return unique allowed asset IDs in desired media order, enough to fill all slots. Image, video and audio fill their own typed slots. A character fills one image and one audio slot (prefer the same index); Drive_AUD reuses the first audio-capable asset. Do not add assets with no compatible slot.
- Judge visual relevance from attached images. For video and audio, use only the user's filename, role and notes; never invent unseen or unheard contents.
- Prefer the speaking or acting main character, then required scene, object, style or composition references.
- Never change a user-assigned image role and never invent an asset ID.
- This is only a suggestion. Explain the ordering briefly in rationale.
"""
    payload = {
        "story": (project.get("settings") or {}).get("story", ""),
        "segment": {
            "id": segment.get("id"),
            "title": segment.get("title"),
            "summary": segment.get("summary"),
            "task_type": segment.get("task_type"),
            "shots": segment.get("shots", []),
            "dialogue": segment.get("dialogue", []),
        },
        "slots_in_order": slots,
        "candidate_assets": [
            {
                "id": asset.get("id"),
                "visible_order": index,
                "character_name": asset.get("character_name", ""),
                "original_name": asset.get("original_name", ""),
                "roles": asset.get("roles", []),
                "notes": asset.get("notes", ""),
                "media_type": asset.get("media_type", "image"),
            }
            for index, asset in enumerate(assets, start=1)
        ],
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def validate_reference_order(result: dict[str, Any], allowed_ids: list[str], slot_count: int, flexible: bool = False) -> list[str]:
    order = result.get("asset_ids")
    if not isinstance(order, list) or (not 1 <= len(order) <= slot_count if flexible else len(order) != slot_count):
        raise ValueError(f"LLM must return exactly {slot_count} reference images")
    normalized = [str(asset_id) for asset_id in order]
    if len(set(normalized)) != len(normalized):
        raise ValueError("LLM returned the same reference image more than once")
    unknown = [asset_id for asset_id in normalized if asset_id not in allowed_ids]
    if unknown:
        raise ValueError(f"LLM returned unknown asset IDs: {', '.join(unknown)}")
    return normalized
