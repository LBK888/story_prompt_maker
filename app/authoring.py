"""Authoring contracts shared by HTTP, CLI and MCP; never performs inference."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator

from .chinese import to_simplified
from .domain import new_id
from .prompt_policy import (
    blueprint_messages, blueprint_schema, h3_messages, h3_prompt_warnings,
    normalize_blueprint, normalize_h3_format, validate_blueprint_content, validate_h3_prompt,
)
from .reference_order import (
    bind_references, bound_reference_asset_ids, picture_asset_ids, reference_candidates,
    reference_order_messages, reference_order_schema, reference_slots,
)
from .segment_images import image_prompt_messages
from .storage import ProjectStore


class AuthoringError(ValueError):
    def __init__(self, code: str, message: str, *, status: int = 422,
                 errors: list[dict[str, str]] | None = None, revision: int | None = None):
        super().__init__(message)
        self.detail = {"code": code, "message": message, "errors": errors or []}
        if revision is not None:
            self.detail["current_revision"] = revision
        self.status = status


def schema_errors(value: Any, schema: dict[str, Any]) -> list[dict[str, str]]:
    return [{"path": "/" + "/".join(str(p) for p in error.absolute_path),
             "code": "schema", "message": error.message}
            for error in sorted(Draft202012Validator(schema).iter_errors(value),
                                key=lambda e: str(list(e.absolute_path)))][:100]


def authoring_schema(project: dict[str, Any]) -> dict[str, Any]:
    settings = project["settings"]
    schema = blueprint_schema(
        bool(settings.get("create_dialogue")), bool(settings.get("create_narration")),
        str(settings.get("dialogue_language", "Chinese")),
        str(settings.get("narration_language", "Chinese")),
    )
    schema["properties"]["segments"]["items"]["properties"]["id"] = {
        "type": "string", "pattern": "^[A-Za-z0-9_-]{1,100}$",
    }
    return schema


def segment_by_id(project: dict[str, Any], segment_id: str | None) -> dict[str, Any]:
    segment = next((s for s in project["segments"] if s["id"] == segment_id), None)
    if segment is None:
        raise AuthoringError("segment_not_found", "Supply an existing segment_id", status=404)
    return segment


class Authoring:
    def __init__(self, store: ProjectStore):
        self.store = store

    def check_revision(self, project: dict[str, Any], expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision != project.get("revision", 0):
            raise AuthoringError("revision_conflict", "Refresh context before submitting changes", status=409,
                                 revision=project.get("revision", 0))

    def context(self, project_id: str, task: str, segment_id: str | None = None,
                *, image_kind: str = "scene", asset_ids: list[str] | None = None,
                current_prompt: str | None = None) -> dict[str, Any]:
        project = deepcopy(self.store.load(project_id))
        schema = None
        segment = None
        if task == "blueprint":
            messages, schema = blueprint_messages(project), authoring_schema(project)
            labelled = [(a["id"], a["id"]) for a in project["assets"] if a.get("roles")]
        else:
            segment = segment_by_id(project, segment_id)
            if task in {"prompt", "optimize"}:
                if task == "optimize":
                    if not current_prompt or not current_prompt.strip():
                        raise AuthoringError("prompt_required", "Supply current_prompt for optimization")
                    # Optimization intentionally excludes storyboard dialogue.
                    format_segment = {**segment, "dialogue": []}
                    minimal = {"settings": {"aspect_ratio": project["settings"]["aspect_ratio"]},
                               "assets": project["assets"]}
                    system = h3_messages(minimal, format_segment)[0]["content"]
                    messages = [{"role": "system", "content": system +
                                 "\nImprove only current_prompt using selected references. Preserve its meaning and all spoken wording. Do not infer dialogue from the stored storyboard."},
                                {"role": "user", "content": current_prompt}]
                else:
                    messages = h3_messages(project, segment)
                labelled = [(f"Picture {i}", aid) for i, aid in enumerate(picture_asset_ids(segment), 1)]
                binding = segment.get("template") or {}
                for label, aid in (("First frame", binding.get("first_frame_asset_id")),
                                   ("Last frame", binding.get("last_frame_asset_id")),
                                   ("Scene reference", segment.get("scene_ref_asset_id"))):
                    if aid and aid not in [item[1] for item in labelled]:
                        labelled.append((label, aid))
            elif task == "references":
                slots = reference_slots((segment.get("template") or {}).get("tokens", []))
                assets = reference_candidates(project, segment)
                messages = reference_order_messages(project, segment, slots, assets)
                schema = reference_order_schema([a["id"] for a in assets], len(slots), flexible=True)
                labelled = [(a["id"], a["id"]) for a in assets]
            elif task == "image":
                if image_kind not in {"scene", "first_frame", "last_frame"}:
                    raise AuthoringError("invalid_image_kind", "Use scene, first_frame or last_frame")
                ids = asset_ids or []
                if len(ids) > 3 or len(set(ids)) != len(ids):
                    raise AuthoringError("invalid_references", "Choose up to three distinct image references")
                known = {a["id"]: a for a in project["assets"]}
                if any(aid not in known or known[aid].get("media_type") not in {"image", "character", "video"} for aid in ids):
                    raise AuthoringError("invalid_references", "Image references must be existing images, characters or videos")
                messages = image_prompt_messages(project, segment, image_kind, bool(ids))
                labelled = [(f"Reference Image {i}", aid) for i, aid in enumerate(ids, 1)]
            else:
                raise AuthoringError("invalid_task", "Use blueprint, prompt, optimize, references or image")
        known = {a["id"]: a for a in project["assets"]}
        references = []
        for label, aid in labelled:
            if aid not in known:
                continue
            asset = deepcopy(known[aid])
            filename = quote(asset["filename"], safe="")
            asset.update({"label": label, "url": f"/api/projects/{project_id}/files/ref_img/{filename}"})
            if asset.get("media_type") in {"image", "character"}:
                asset["preview_url"] = f"/api/agent/projects/{project_id}/assets/{aid}/preview"
            references.append(asset)
        return {"contract_version": 1, "inference": "caller", "project_id": project_id,
                "revision": project.get("revision", 0), "task": task, "segment_id": segment_id,
                "messages": messages, "output_schema": schema, "references": references,
                "existing_segments": [{"id": s["id"], "index": s["index"], "title": s["title"]}
                                      for s in project["segments"]],
                "submission_notes": ["Use the caller's model; no server-side inference is needed.",
                                     "Return expected_revision on every authoring mutation.",
                                     "When updating a blueprint, retain every existing segment ID.",
                                     "Reference roles are user-assigned facts. Do not guess roles."]}

    def apply_blueprint(self, project_id: str, raw: dict[str, Any], expected_revision: int,
                        *, validate_only: bool = False) -> dict[str, Any]:
        with self.store._lock:
            project = self.store.load(project_id)
            self.check_revision(project, expected_revision)
            errors = schema_errors(raw, authoring_schema(project))
            if errors:
                raise AuthoringError("invalid_blueprint", "Blueprint failed schema validation", errors=errors)
            candidate = deepcopy(raw)
            errors = [{"path": "/segments", "code": "content", "message": e}
                      for e in validate_blueprint_content(candidate)]
            if errors:
                raise AuthoringError("invalid_blueprint", "Blueprint failed content validation", errors=errors)
            existing = {s["id"]: s for s in project["segments"]}
            ids = [s.get("id") for s in candidate["segments"]]
            if existing and (any(not sid for sid in ids) or not set(existing).issubset(ids)):
                raise AuthoringError("segment_ids_required", "Updates must retain all existing segment IDs; use explicit segment deletion separately")
            for segment in candidate["segments"]:
                segment.setdefault("id", new_id("segment"))
            ids = [s["id"] for s in candidate["segments"]]
            if len(set(ids)) != len(ids):
                raise AuthoringError("duplicate_segment_id", "Segment IDs must be unique")
            settings = project["settings"]
            candidate = normalize_blueprint(candidate, settings.get("speech_speed", "normal"))
            errors = [{"path": "/segments", "code": "content", "message": e}
                      for e in validate_blueprint_content(candidate)]
            known_ids = {a["id"] for a in project["assets"]}
            for index, segment in enumerate(candidate["segments"]):
                if not set(segment["reference_asset_ids"]).issubset(known_ids):
                    errors.append({"path": f"/segments/{index}/reference_asset_ids", "code": "unknown_asset", "message": "Unknown reference asset ID"})
                for kind, flag, language in (("dialogue", "create_dialogue", "dialogue_language"),
                                              ("voiceover", "create_narration", "narration_language")):
                    lines = [d for d in segment["dialogue"] if d["kind"] == kind and d["spoken_text"].strip()]
                    if settings.get(flag) and not lines:
                        errors.append({"path": f"/segments/{index}/dialogue", "code": "missing_speech", "message": f"Each segment requires {kind}"})
                    if any(d["language"] != settings.get(language, "Chinese") for d in lines):
                        errors.append({"path": f"/segments/{index}/dialogue", "code": "speech_language", "message": f"Wrong {kind} language"})
            total = sum(s["duration_seconds"] for s in candidate["segments"])
            if total < settings["minimum_duration_seconds"]:
                errors.append({"path": "/segments", "code": "duration", "message": f"Total {total}s is below minimum {settings['minimum_duration_seconds']}s"})
            if errors:
                raise AuthoringError("invalid_blueprint", "Blueprint failed content validation", errors=errors)
            warnings = []
            if total > max(settings["minimum_duration_seconds"] * 1.10, settings["minimum_duration_seconds"] + 1):
                warnings.append("Total duration exceeds the normal +10% range")
            bible_changed = any((project.get("blueprint") or {}).get(k) != candidate[k]
                                for k in ("continuity_bible", "sound_bible"))
            authored_keys = set(raw["segments"][0]) - {"id"}
            invalidate_later = bible_changed
            for segment in candidate["segments"]:
                speech = sum(d["estimated_seconds"] for d in segment["dialogue"])
                segment["dialogue_estimated_seconds"] = round(speech, 2)
                if speech > segment["duration_seconds"]:
                    warnings.append(f"{segment['id']}: speech exceeds segment duration")
                old = existing.get(segment["id"])
                if old:
                    changed = bible_changed or old.get("index") != segment["index"] or any(old.get(k) != segment.get(k) for k in authored_keys)
                    # Preserve every execution field, including takes, active output,
                    # reference binding and prompt; replace only authored plan fields.
                    authored = {k: segment[k] for k in authored_keys | {"id", "index", "dialogue_estimated_seconds"} if k in segment}
                    segment.clear()
                    segment.update(deepcopy(old))
                    segment.update(authored)
                    invalidate_later = invalidate_later or changed
                    if invalidate_later:
                        segment["prompt_status"] = "stale"
                segment["blueprint_source"] = "agent"
            if validate_only:
                return {"valid": True, "warnings": warnings, "revision": project.get("revision", 0)}
            project["blueprint"] = {k: v for k, v in candidate.items() if k != "segments"}
            project["blueprint"].update({"planned_duration_seconds": total, "timing_conflicts": warnings, "source": "agent"})
            project["segments"] = candidate["segments"]
            project["blueprint_status"] = "needs_timing_review" if warnings else "ready"
            self.store.save(project, expected_revision=expected_revision)
            return {"project": project, "warnings": warnings, "revision": project["revision"]}

    def set_prompt(self, project_id: str, segment_id: str, prompt: str, expected_revision: int,
                   *, validate_only: bool = False) -> dict[str, Any]:
        with self.store._lock:
            project = self.store.load(project_id)
            self.check_revision(project, expected_revision)
            segment = segment_by_id(project, segment_id)
            normalized = normalize_h3_format(to_simplified(prompt))
            errors = validate_h3_prompt(normalized, segment)
            if not normalized.strip():
                errors = ["Prompt cannot be empty"]
            if errors:
                raise AuthoringError("invalid_prompt", "H3 prompt validation failed", errors=[
                    {"path": "/prompt", "code": "h3", "message": e} for e in errors])
            warnings = h3_prompt_warnings(normalized, segment)
            if not validate_only:
                if normalized != segment.get("prompt"):
                    for later in project["segments"]:
                        if later["index"] > segment["index"] and later.get("prompt"):
                            later["prompt_status"] = "stale"
                segment.update({"prompt": normalized, "prompt_source": "agent", "prompt_status": "ready",
                                "prompt_validation_errors": [], "prompt_validation_warnings": warnings})
                self.store.save(project, expected_revision=expected_revision)
            return {"segment": segment if not validate_only else None, "valid": True,
                    "prompt": normalized, "warnings": warnings, "revision": project.get("revision", 0)}

    def bind(self, project_id: str, segment_id: str, binding: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        allowed = {"image_order", "first_frame_asset_id", "last_frame_asset_id", "scene_ref_asset_id"}
        errors = schema_errors(binding, {"type": "object", "additionalProperties": False, "properties": {
            "image_order": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            **{k: {"type": ["string", "null"]} for k in allowed - {"image_order"}}}})
        if errors:
            raise AuthoringError("invalid_binding", "Invalid reference binding", errors=errors)
        with self.store._lock:
            project = self.store.load(project_id)
            self.check_revision(project, expected_revision)
            segment = segment_by_id(project, segment_id)
            if not segment.get("template"):
                raise AuthoringError("template_required", "Assign a workflow template first")
            ids = list(binding.get("image_order", [])) + [binding[k] for k in allowed - {"image_order"} if binding.get(k)]
            if not set(ids).issubset({a["id"] for a in project["assets"]}):
                raise AuthoringError("unknown_asset", "Binding contains unknown asset IDs")
            before = deepcopy(segment["template"]), segment.get("scene_ref_asset_id")
            segment["template"].update({k: v for k, v in binding.items() if k != "scene_ref_asset_id"})
            if "scene_ref_asset_id" in binding:
                segment["scene_ref_asset_id"] = binding["scene_ref_asset_id"]
            try:
                bind_references(segment["template"], project["assets"], require_complete=True)
            except ValueError as exc:
                raise AuthoringError("invalid_binding", str(exc)) from exc
            segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
            if before != (segment["template"], segment.get("scene_ref_asset_id")) and segment.get("prompt"):
                segment["prompt_status"] = "stale"
            self.store.save(project, expected_revision=expected_revision)
            return {"segment": segment, "revision": project["revision"]}
