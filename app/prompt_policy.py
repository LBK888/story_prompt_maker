from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from .chinese import to_simplified
from .timing import estimate_speech_seconds, partition_duration
from .reference_order import picture_asset_ids, picture_reference_context, picture_slots, media_reference_context
from .prompt_supplements import storytelling_supplement


OFFICIAL_SOURCE = (
    "MiniMax-AI/MiniMax-H3 h3-prompt-writing, frozen for this application from "
    "commit b7227fa6a6206e9fb30562383d39e53cf3866a48"
)

PRESET_RULES = {
    "none": "Use only the H3 core contract and the requested visual style.",
    "minimalist_product_ad": "Lock product identity and materials; use negative space, one main action per beat, and no invented claims or copy.",
    "3d_animation": "Preserve character and landmark continuity, readable silhouettes, anticipation, follow-through, and no more than three important active characters unless required.",
    "brand_promo": "Use only supplied or visibly verified brand facts and exact copy. Demonstrate concrete requested benefits; invent no claims, metrics, logos, or functions.",
    "music_video_typography": "Lock supplied lyrics, keep typography spatial and readable, use only evidenced rhythm, and do not claim audio analysis.",
    "coop_game_intro": "Lock two player identities, placement, exact names and UI copy; invent no gameplay mechanics or working interactions.",
    "paper_collage": "Use tactile halftone paper shapes, stop-motion assembly, readable metaphors, paper friction and restrained text.",
    "papercraft_stop_motion": "Use a layered paper diorama with stable material, folds, lighting, depth and physically legible paper mechanisms.",
    "handdrawn_live_fusion": "Keep one adjacent live-action space, make contact early, preserve one entity through drawn morphs, and use playful non-horror hand-drawn traces.",
}

BLUEPRINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "story_summary", "continuity_bible", "sound_bible", "segments"],
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "story_summary": {"type": "string", "minLength": 1},
        "continuity_bible": {
            "type": "object",
            "additionalProperties": False,
            "required": ["characters", "locations", "global_visual_rules"],
            "properties": {
                "characters": {"type": "array", "items": {"type": "object"}},
                "locations": {"type": "array", "items": {"type": "object"}},
                "global_visual_rules": {"type": "array", "items": {"type": "string"}},
            },
        },
        "sound_bible": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ambience", "music", "continuity"],
            "properties": {
                "ambience": {"type": "string"},
                "music": {"type": "string"},
                "continuity": {"type": "string"},
            },
        },
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title", "summary", "duration_seconds", "task_type", "shots",
                    "dialogue", "continuity_from_previous", "reference_asset_ids",
                ],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "duration_seconds": {"type": "integer", "minimum": 4, "maximum": 25},
                    "task_type": {"enum": ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"]},
                    "shots": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["start_seconds", "framing", "visual", "action", "camera", "audio", "speaker_ids"],
                            "properties": {
                                "start_seconds": {"type": "number", "minimum": 0},
                                "framing": {"type": "string", "minLength": 1},
                                "visual": {"type": "string", "minLength": 1},
                                "action": {"type": "string"},
                                "camera": {"type": "string", "minLength": 1},
                                "audio": {"type": "string"},
                                "speaker_ids": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    "dialogue": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["speaker_id", "kind", "language", "original_text", "spoken_text", "estimated_seconds"],
                            "properties": {
                                "speaker_id": {"type": "string"},
                                "kind": {"enum": ["dialogue", "voiceover"]},
                                "language": {"enum": ["Chinese", "English"]},
                                "original_text": {"type": "string"},
                                "spoken_text": {"type": "string"},
                                "estimated_seconds": {"type": "number", "minimum": 0},
                            },
                        },
                    },
                    "continuity_from_previous": {"type": "boolean"},
                    "reference_asset_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}


def blueprint_schema(
    require_dialogue: bool = False,
    require_narration: bool = False,
    dialogue_language: str = "Chinese",
    narration_language: str = "Chinese",
) -> dict[str, Any]:
    """Build a structured-output contract that matches the user's audio choices.

    Natural-language MUST instructions are not sufficient with a small local model:
    if the schema permits an empty array, structured decoding can still choose it.
    """
    schema = deepcopy(BLUEPRINT_SCHEMA)
    dialogue = schema["properties"]["segments"]["items"]["properties"]["dialogue"]
    line = dialogue["items"]
    allowed_kinds: list[str] = []
    allowed_languages: list[str] = []
    if require_dialogue:
        allowed_kinds.append("dialogue")
        allowed_languages.append(dialogue_language)
    if require_narration:
        allowed_kinds.append("voiceover")
        allowed_languages.append(narration_language)
    if not allowed_kinds:
        dialogue["maxItems"] = 0
        return schema
    dialogue["minItems"] = 2 if require_dialogue and require_narration else 1
    line["properties"]["kind"]["enum"] = allowed_kinds
    line["properties"]["language"]["enum"] = list(dict.fromkeys(allowed_languages))
    return schema


def _asset_context(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "roles": item.get("roles", []),
            "bindings": item.get("bindings", []),
            "notes": item.get("notes", ""),
            "character_name": item.get("character_name", ""),
            "original_name": item.get("original_name", ""),
        }
        for item in assets
    ]


def _sanitize_h3_reference_ids(value: Any, segment: dict[str, Any]) -> Any:
    aliases = {
        asset_id: f"<Picture {index}>"
        for index, asset_id in enumerate(picture_asset_ids(segment), start=1)
    }
    if isinstance(value, dict):
        return {key: _sanitize_h3_reference_ids(item, segment) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_h3_reference_ids(item, segment) for item in value]
    if not isinstance(value, str):
        return value
    sanitized = value
    for asset_id, alias in aliases.items():
        sanitized = sanitized.replace(asset_id, alias)
    return re.sub(r"\basset_[A-Za-z0-9_-]+\b", "the reference image", sanitized)


def blueprint_messages(
    project: dict[str, Any],
    repair_errors: list[str] | None = None,
    previous_blueprint: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    settings = project["settings"]
    minimum = int(settings["minimum_duration_seconds"])
    preferred = int(settings.get("segment_duration_seconds", 15))
    suggested = partition_duration(minimum, preferred)
    system = f"""You are a conservative audiovisual storyboard planner for MiniMax H3.
Return only JSON matching the supplied schema. Do not write a final H3 prompt yet.

Hard rules:
- The requested duration is a minimum. Plan between {minimum} and {minimum * 1.10:.1f} seconds where possible.
- Every generation segment is 4-25 seconds. Prefer {preferred} seconds per segment. Suggested neutral partition: {suggested}.
- Normally introduce a new shot every 2-3 seconds, but dialogue clarity overrides cutting frequency.
- On-screen dialogue must show the actual speaker in a medium close-up, upper-body close-up, or facial close-up. Never turn it into background narration.
- Preserve supplied dialogue exactly except that all Chinese spoken or visible text must be Simplified Chinese.
- Estimate Chinese at 3.5 Han characters/second and English at 2.5 words/second, plus punctuation pauses and 0.3 seconds breathing room.
- A dialogue sentence may remain in one clear speaker shot for 3-6 seconds. If speech crosses a cut, keep the same speaker and explicit audio continuity.
- Do not invent dialogue unless create_dialogue is true. Do not invent voiceover unless create_narration is true.
- When create_dialogue is true, EVERY segment MUST contain at least one natural on-screen dialogue line in the requested dialogue_language. Put it in the segment dialogue array with kind=dialogue, exact original_text/spoken_text, a stable speaker_id, and enough shot time for delivery.
- When create_narration is true, EVERY segment MUST contain at least one narration line in the requested narration_language. Put it in the segment dialogue array with kind=voiceover. When either option is false, create none of that kind.
- Never invent readable scene text. Never infer a reference image role; use only the user's role and binding.
- Keep events, identities, outcomes, and locked language faithful to the story.
- Use reference asset IDs exactly as supplied only inside each segment's reference_asset_ids array. Never write an asset ID in a title, summary, shot visual/action/camera/audio field, dialogue, continuity rule, or other prose.
- Set task_type from actual inputs: T2VA no image; I2VA first frame; FL2VA first+last; L2VA last; Ref2VA reusable subject/scene/style references.
- continuity_from_previous is true only for continuous scene/action/dialogue where the prior active take's last frame should become this segment's first frame.
- No more than 100 segments. Projects over 12 segments are valid but expensive.
- Every segment title and summary must describe actual story content. Never use placeholders such as "Segment 1" or "片段 01" as the summary.
- Every shot must contain a concrete, non-empty visual description, framing and camera instruction.
- Shot start_seconds are relative to their own segment: first is exactly 0, later values are strictly increasing and below the segment duration.
- Build a canonical global character bible before planning clips. Every principal character entry must lock one global character/speaker ID, apparent age, facial and skin features, hair color/style/length, body build, complete wardrobe colors/materials, footwear, accessories, and recurring props.
- Build a canonical location bible for every recurring setting. Each entry must lock the exact place, scale and spatial layout, wall/floor/ceiling materials, doors/windows/furniture/fixed landmarks, time of day, weather where relevant, and lighting direction/color/quality.
- Every segment must contain enough canonical character, location, lighting, and incoming-state detail for its later H3 prompt to stand alone without access to another clip. Reuse the same concrete wording across segments; do not use continuity shorthand.
"""
    user = {
        "story": settings.get("story", ""),
        "minimum_duration_seconds": minimum,
        "style_category": settings.get("style_category"),
        "style_notes": settings.get("style_notes", ""),
        "aspect_ratio": settings.get("aspect_ratio"),
        "expansion_mode": settings.get("expansion_mode"),
        "official_preset": settings.get("official_preset"),
        "create_dialogue": settings.get("create_dialogue", False),
        "dialogue_language": settings.get("dialogue_language", "Chinese"),
        "create_narration": settings.get("create_narration", False),
        "narration_language": settings.get("narration_language", "Chinese"),
        "reference_assets": _asset_context(project.get("assets", [])),
    }
    if repair_errors:
        system += "\nThe previous blueprint was rejected. Repair every listed error; do not return an empty schema skeleton."
        user["validation_errors_to_repair"] = repair_errors
        user["previous_invalid_blueprint"] = previous_blueprint
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def validate_blueprint_content(
    blueprint: dict[str, Any],
    require_dialogue: bool = False,
    require_narration: bool = False,
) -> list[str]:
    errors: list[str] = []
    segments = blueprint.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["Blueprint must contain at least one segment"]
    for segment_index, segment in enumerate(segments, start=1):
        label = str(segment.get("id") or f"segment {segment_index}")
        title = str(segment.get("title") or "").strip()
        summary = str(segment.get("summary") or "").strip()
        if not title:
            errors.append(f"{label}: title is empty")
        if not summary or re.fullmatch(r"(?:片段|segment)\s*0*\d+", summary, re.IGNORECASE):
            errors.append(f"{label}: summary is empty or only a placeholder")
        shots = segment.get("shots")
        if not isinstance(shots, list) or not shots:
            errors.append(f"{label}: shots are empty")
            continue
        starts: list[float] = []
        for shot_index, shot in enumerate(shots, start=1):
            visual = str(shot.get("visual") or "").strip()
            if not visual:
                errors.append(f"{label} shot {shot_index}: visual is empty")
            if not str(shot.get("framing") or "").strip():
                errors.append(f"{label} shot {shot_index}: framing is empty")
            if not str(shot.get("camera") or "").strip():
                errors.append(f"{label} shot {shot_index}: camera is empty")
            try:
                starts.append(float(shot.get("start_seconds")))
            except (TypeError, ValueError):
                errors.append(f"{label} shot {shot_index}: start_seconds is invalid")
        duration = float(segment.get("duration_seconds") or 0)
        if starts and (starts[0] != 0 or starts != sorted(set(starts)) or any(value < 0 or value >= duration for value in starts)):
            errors.append(f"{label}: shot start_seconds must begin at 0 and be strictly increasing below duration")
    dialogue_lines = [
        line
        for segment in segments
        for line in segment.get("dialogue", [])
        if isinstance(line, dict) and str(line.get("spoken_text") or line.get("original_text") or "").strip()
    ]
    if require_dialogue and not any(line.get("kind") == "dialogue" for line in dialogue_lines):
        errors.append("create_dialogue is enabled but the blueprint contains no dialogue lines")
    if require_narration and not any(line.get("kind") == "voiceover" for line in dialogue_lines):
        errors.append("create_narration is enabled but the blueprint contains no voiceover lines")
    return errors


def normalize_blueprint(
    blueprint: dict[str, Any],
    speech_speed: str = "normal",
    preserve_blank_summary: bool = False,
) -> dict[str, Any]:
    default_task_type = str(blueprint.get("task_type") or "T2VA")
    for index, segment in enumerate(blueprint.get("segments", []), start=1):
        segment["id"] = segment.get("id") or f"segment_{index:03d}"
        segment["index"] = index
        segment["title"] = str(segment.get("title") or f"片段 {index:02d}")
        segment["task_type"] = str(segment.get("task_type") or default_task_type)
        if segment["task_type"] not in {"T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"}:
            segment["task_type"] = "T2VA"
        segment.setdefault("continuity_from_previous", index > 1)
        segment.setdefault("reference_asset_ids", [])
        segment.setdefault("prompt", "")
        segment.setdefault("prompt_status", "missing")
        segment.setdefault("prompt_source", "llm")
        segment.setdefault("prompt_validation_errors", [])
        segment.setdefault("prompt_validation_warnings", [])
        segment.setdefault("template", None)
        segment.setdefault("takes", [])
        segment.setdefault("active_take_id", None)
        segment.setdefault("status", "planned")

        raw_shots = [item for item in segment.get("shots", []) if isinstance(item, dict)]
        starts = [
            float(item.get("start_seconds", item.get("time_start", 0)) or 0)
            for item in raw_shots
        ]
        time_origin = min(starts, default=0)
        canonical_shots: list[dict[str, Any]] = []
        embedded_dialogue: list[dict[str, Any]] = []
        for shot_index, shot in enumerate(raw_shots):
            description = str(
                shot.get("visual")
                or shot.get("visual_description")
                or shot.get("description")
                or shot.get("action")
                or ""
            ).strip()
            framing = str(shot.get("framing") or shot.get("camera") or "medium shot").strip()
            speaker_ids = shot.get("speaker_ids")
            if not isinstance(speaker_ids, list):
                speaker = str(shot.get("speaker") or "").strip()
                speaker_ids = [speaker] if speaker else []
            start = max(0.0, starts[shot_index] - time_origin)
            canonical_shots.append(
                {
                    "start_seconds": start,
                    "framing": framing,
                    "visual": description,
                    "action": str(shot.get("action") or "").strip(),
                    "camera": str(shot.get("camera") or shot.get("camera_instruction") or framing).strip(),
                    "audio": str(shot.get("audio") or "").strip(),
                    "speaker_ids": [str(item) for item in speaker_ids],
                }
            )
            spoken = str(shot.get("dialogue") or "").strip()
            if spoken:
                language = "Chinese" if re.search(r"[\u3400-\u9fff]", spoken) else "English"
                speaker = str(shot.get("speaker") or f"S{len(embedded_dialogue) + 1}").strip()
                embedded_dialogue.append(
                    {
                        "speaker_id": speaker,
                        "kind": "dialogue",
                        "language": language,
                        "original_text": spoken,
                        "spoken_text": spoken,
                    }
                )
        segment["shots"] = canonical_shots

        summary = str(segment.get("summary") or "").strip()
        if not summary and not preserve_blank_summary and segment.get("creation_mode") != "manual_blank":
            summary_parts = [item["visual"] for item in canonical_shots if item["visual"]]
            summary = " ".join(summary_parts[:2]).strip() or segment["title"]
        segment["summary"] = summary

        dialogue = segment.get("dialogue")
        if not isinstance(dialogue, list) or not dialogue:
            dialogue = embedded_dialogue
            segment["dialogue"] = dialogue
        for line in dialogue:
            if not isinstance(line, dict):
                continue
            original = str(line.get("original_text", ""))
            spoken = str(line.get("spoken_text", original))
            language = str(line.get("language") or ("Chinese" if re.search(r"[\u3400-\u9fff]", spoken) else "English"))
            line["language"] = language
            line.setdefault("kind", "dialogue")
            line.setdefault("speaker_id", "S1")
            line.setdefault("original_text", spoken)
            if language == "Chinese":
                spoken = to_simplified(spoken)
            line["spoken_text"] = spoken
            line["estimated_seconds"] = estimate_speech_seconds(spoken, speech_speed)
    return blueprint


def h3_messages(project: dict[str, Any], segment: dict[str, Any], repair_errors: list[str] | None = None) -> list[dict[str, Any]]:
    settings = project["settings"]
    task_type = str(segment.get("task_type") or (project.get("blueprint") or {}).get("task_type") or "T2VA")
    segment["task_type"] = task_type
    duration = int(segment["duration_seconds"])
    preset = str(segment.get("official_preset") or settings.get("official_preset", "AUTO"))
    preset_rule = PRESET_RULES.get(preset, PRESET_RULES["none"])
    fields = (
        "subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music"
        if task_type == "Ref2VA"
        else "integrated_multimodal_description, overall_soundscape, non_diegetic_music"
    )
    identity_contract = (
        "For each person supplied by <Picture N>, treat that image as the authority for facial identity, skin, hair, body, outfit, and accessories. In subject_definitions bind the person to <Subject N> and <Picture N> with a concise consistency lock; do not reconstruct their appearance or clothing in words. In summary, retention_analysis, detailed_description, and shot descriptions refer to <Subject N> without repeating facial, hair, body, or wardrobe details unless the user explicitly requests a change. Prior text must not override the image. Fully describe only people who have no usable reference image. Restate the scene, action, lighting, props, and incoming physical state in every clip."
        if task_type == "Ref2VA"
        else "Every final clip prompt is a standalone generation specification. Assume the video model has zero knowledge of any other clip. In every clip, fully restate all visible principal characters, complete wardrobe and accessories, exact location, time of day, lighting, scene geometry, recurring props, starting positions, and incoming physical state."
    )
    system = f"""Write one final MiniMax H3 prompt. Return only the prompt, no Markdown or explanation.
Source policy: {OFFICIAL_SOURCE}.

Output contract:
- Task is {task_type}; exact duration is {duration} seconds; aspect ratio is {settings.get('aspect_ratio')}.
- Use exactly these fields in this order: {fields}.
- T2VA begins with its first field. I2VA begins with the exact first-frame alignment sentence. FL2VA and L2VA begin with their official picture-alignment sentence using {duration:.2f} seconds.
- [Shot 1] has no timestamp. Every later shot begins `[Shot N] At MM:SS.mmm,` with strictly increasing times below {duration} seconds.
- English is mandatory for field names and descriptive prose. Chinese dialogue, lyrics, and visible text must be Simplified Chinese.
- Actual vocal sources receive stable (S1), (S2), ... global story identities. Use IDs supplied by the segment or continuity bible and never renumber them by local speaking order. Only assign first-vocal order when the project has no established ID. Spoken content is `<d>[Chinese] ...</d>` or `<d>[English] ...</d>`.
- On-screen speech must visibly frame the speaker from upper body to face, preferably a medium close-up or close-up, with readable lip, jaw and expression movement.
- Voiceover uses the exact phrase `says in an off-screen voiceover` and visible related people keep lips closed.
- Speech crossing a cut uses <scenetrans> on both sides and explicitly continuous audio. Use <cutoff> only at the video ending.
- Preserve locked spoken wording. Do not invent dialogue, narration, lyrics, claims, logos, metrics or readable text beyond the permissions in the supplied plan.
- overall_soundscape is 1-4 English sentences for ambience, action sounds, and nonverbal human sounds; it excludes dialogue and music.
- non_diegetic_music is 1-3 English sentences specifying instrumentation, tempo, rhythm and dynamics, or N/A.
- Prefer camera motion over a cut for small framing changes. Describe camera type, amplitude and speed naturally.
- Fit every action and sound within {duration} seconds.
- Reference image roles are user facts. `<Picture 1>`, `<Picture 2>`, ... are positional aliases: they map exactly to REF_IMG1, REF_IMG2, ... in the supplied reference_assets order.
- Never output an internal identifier such as `asset_...`; ComfyUI cannot see it. Refer to an input image only as its exact `<Picture N>` alias.
- In Ref2VA subject_definitions, define every supplied `<Picture N>:`. Define each reusable person/object as `<Subject N>:` and explicitly say which `<Picture N>` it comes from. For a referenced person, use a concise identity lock such as `<Subject 1>: the young woman shown in <Picture 1>, maintain 100% absolute consistency in facial identity, skin texture, hairstyle, body anatomy, existing outfit, and accessories. No outfit changes. Photorealistic pores, individual hair strands, natural fabric physics. No CGI, no anime, no plastic skin.` Then define `<Picture 1>:` as that person's visual reference, without describing their appearance or wardrobe.
- Reusable character/scene/style content is <Subject N>; a concrete input image, frame or composition anchor is <Picture N>.
- Ref2VA uses relationship markers fully_preserved, partially_preserved, attribute_transfer, or weak_reference.
- Preserve recurring subject identity, props, scene design, lighting, and temporal state established in preceding_segments. For a referenced person, the supplied picture controls facial features, body, hair, and outfit; change those only when the current segment explicitly requires it.
- Treat preceding ready prompts as continuity evidence for scene and action. Do not introduce a new location design or character variation merely for novelty, and do not copy a competing written portrait over a supplied picture.
- {identity_contract}
- Never use shorthand such as `same as before`, `the previous scene`, `continues from the last clip`, or an equivalent phrase in any language. Copy the canonical concrete facts instead.
- For T2VA, which has no visual reference, `integrated_multimodal_description` must establish that complete identity-and-scene specification before the first shot action.
- Creative preset guidance: {preset_rule}
"""
    supplement = storytelling_supplement(task_type)
    if supplement:
        system += f"\n\n{supplement}\n"
    preceding_segments: list[dict[str, Any]] = []
    for prior in project.get("segments", []):
        if prior is segment or (
            segment.get("id") is not None and prior.get("id") == segment.get("id")
        ):
            break
        preceding_segments.append(
            {
                "id": prior.get("id"),
                "index": prior.get("index"),
                "title": prior.get("title"),
                "summary": prior.get("summary"),
                "shots": prior.get("shots", []),
                "dialogue": prior.get("dialogue", []),
                "prompt": re.sub(
                    r"\basset_[A-Za-z0-9_-]+\b",
                    "the referenced image",
                    str(prior.get("prompt", "")),
                ) if prior.get("prompt_status") == "ready" else "",
            }
        )
    segment_payload = {
        key: segment.get(key)
        for key in (
            "id", "index", "title", "summary", "duration_seconds", "task_type", "shots", "dialogue",
            "continuity_from_previous", "timing_warning", "dialogue_estimated_seconds",
        )
        if key in segment
    }
    payload = {
        "story": settings.get("story", ""),
        "expansion_mode": segment.get("expansion_mode", settings.get("expansion_mode", "balanced")),
        "style_category": segment.get("style_category", settings.get("style_category")),
        "style_notes": segment.get("style_notes", settings.get("style_notes", "")),
        "continuity_bible": (project.get("blueprint") or {}).get("continuity_bible", {}),
        "sound_bible": (project.get("blueprint") or {}).get("sound_bible", {}),
        "preceding_segments": preceding_segments,
        "segment": segment_payload,
        "reference_assets": picture_reference_context(project, segment),
        "reference_audio_video": media_reference_context(project, segment),
    }
    payload = _sanitize_h3_reference_ids(payload, segment)
    if repair_errors:
        payload["validation_errors_to_repair"] = repair_errors
        system += "\nRepair only the listed validation errors. Preserve all locked facts and text."
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def normalize_h3_format(prompt: str) -> str:
    """Repair a few deterministic H3 syntax omissions without rewriting content."""
    field_names = (
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "integrated_multimodal_description",
        "overall_soundscape",
        "non_diegetic_music",
    )
    repaired = prompt
    for field in field_names:
        repaired = re.sub(rf"(?m)^(\s*{re.escape(field)})\s*$", rf"\1:", repaired)

    def add_dialogue_language(match: re.Match[str]) -> str:
        spoken = match.group(1).strip()
        language = "Chinese" if re.search(r"[\u3400-\u9fff]", spoken) else "English"
        return f"<d>[{language}] {spoken}</d>"

    repaired = re.sub(r"<d>(?!\s*\[(?:Chinese|English)\])(.*?)</d>", add_dialogue_language, repaired, flags=re.DOTALL)
    return repaired.strip()


def validate_h3_prompt(prompt: str, segment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    task_type = str(segment.get("task_type") or "T2VA")
    expected = (
        ["subject_definitions", "summary", "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music"]
        if task_type == "Ref2VA"
        else ["integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"]
    )
    positions: list[int] = []
    for field in expected:
        matches = list(re.finditer(rf"(?m)^{re.escape(field)}:\s*", prompt))
        if len(matches) != 1:
            errors.append(f"Expected exactly one `{field}:` field")
        else:
            positions.append(matches[0].start())
    if len(positions) == len(expected) and positions != sorted(positions):
        errors.append("H3 fields are not in the required order")
    if re.search(r"\basset_[A-Za-z0-9_-]+\b", prompt):
        errors.append("Prompt contains an internal asset ID; use <Picture N> positional aliases instead")
    if re.search(
        r"(?i)\b(?:same as (?:before|previously|the previous (?:clip|scene|shot))|"
        r"(?:continue|continues|continued|continuing) from (?:before|the (?:last|previous) (?:clip|scene|shot))|"
        r"the (?:same|previous) (?:room|location|scene|street|costume))\b|"
        r"(?:和|與)?(?:上一|前一)(?:個|段)?(?:片段|場景|鏡頭)(?:相同|一樣)|"
        r"(?:承接|延續)(?:上一|前一)(?:個|段)?(?:片段|場景|鏡頭)",
        prompt,
    ):
        detail = "reference-linked subjects, scene, lighting, and incoming state" if task_type == "Ref2VA" else "complete character, wardrobe, scene, lighting, and incoming state"
        errors.append(f"Prompt relies on continuity shorthand; restate the {detail}")
    if task_type == "Ref2VA":
        picture_count = len(picture_asset_ids(segment))
        slots = picture_slots(segment)
        for index in range(1, picture_count + 1):
            if not re.search(rf"<Picture\s+{index}>\s*[:：]", prompt):
                errors.append(f"subject_definitions must define <Picture {index}> for {slots[index - 1]}")
        mentioned = {int(value) for value in re.findall(r"<Picture\s+(\d+)>", prompt)}
        unexpected = sorted(index for index in mentioned if index < 1 or index > picture_count)
        if unexpected:
            errors.append(f"Prompt references Picture indexes outside the {picture_count} supplied images: {unexpected}")
        subject_section = prompt.split("summary:", 1)[0]
        for subject, definition in re.findall(r"(?m)^<Subject\s+(\d+)>\s*[:：]\s*(.+)$", subject_section):
            if not re.search(r"<Picture\s+\d+>", definition):
                errors.append(f"<Subject {subject}> must identify its source <Picture N>")
    shots = list(re.finditer(r"\[Shot\s+(\d+)\](?:\s+At\s+(\d{2}):(\d{2})\.(\d{3}),)?", prompt))
    if not shots:
        errors.append("No [Shot N] timeline was found")
    else:
        numbers = [int(match.group(1)) for match in shots]
        if numbers != list(range(1, len(numbers) + 1)):
            errors.append("Shot numbers must be consecutive from 1")
        if shots[0].group(2) is not None:
            errors.append("[Shot 1] must not have a timestamp")
        previous = 0.0
        duration = float(segment["duration_seconds"])
        for match in shots[1:]:
            if match.group(2) is None:
                errors.append(f"[Shot {match.group(1)}] is missing a timestamp")
                continue
            seconds = int(match.group(2)) * 60 + int(match.group(3)) + int(match.group(4)) / 1000
            if seconds <= previous or seconds >= duration:
                errors.append(f"[Shot {match.group(1)}] timestamp {seconds:.3f} is outside the valid increasing timeline")
            previous = seconds
    dialogue = segment.get("dialogue", [])
    for line in dialogue:
        spoken = str(line.get("spoken_text", "")).strip()
        if spoken and spoken not in prompt:
            errors.append(f"Locked spoken text is missing: {spoken}")
    if any(line.get("kind") == "dialogue" for line in dialogue):
        lower = prompt.lower()
        if "<d>[" not in prompt or "(s" not in lower:
            errors.append("Dialogue requires stable speaker IDs and <d>[Language] tags")
        if not any(term in lower for term in ("close-up", "close up", "medium close", "upper-body", "upper body")):
            errors.append("On-screen dialogue requires an upper-body-to-face close framing")
    if task_type in {"FL2VA", "L2VA"} and not prompt.startswith("How the reference pictures align"):
        errors.append(f"{task_type} prompt is missing the official alignment instruction")
    return errors


def h3_prompt_warnings(prompt: str, segment: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    task_type = str(segment.get("task_type") or "T2VA")
    if task_type == "I2VA" and not prompt.startswith("For the target video, at 0.00 seconds"):
        warnings.append("I2VA prompt is missing the official first-frame instruction")
    return warnings
