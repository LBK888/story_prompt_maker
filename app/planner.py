from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .chinese import to_simplified
from .llama_service import LlamaServerManager
from .media import attach_images, ensure_analysis_copy
from .media_types import media_type
from .reference_order import resolve_references, bind_references
from .prompt_policy import (
    blueprint_schema,
    blueprint_messages,
    h3_prompt_warnings,
    h3_messages,
    normalize_blueprint,
    normalize_h3_format,
    validate_blueprint_content,
    validate_h3_prompt,
)
from .reference_order import (
    bound_reference_asset_ids,
    media_reference_context,
    picture_asset_ids,
    picture_reference_context,
    reference_candidates,
    reference_order_messages,
    reference_order_schema,
    reference_slots,
    validate_reference_order,
)
from .storage import ProjectStore


class PlanningError(RuntimeError):
    pass


class StoryPlanner:
    def __init__(self, store: ProjectStore, llama: LlamaServerManager) -> None:
        self.store = store
        self.llama = llama

    def _asset_images(
        self,
        project_id: str,
        project: dict[str, Any],
        asset_ids: set[str] | None = None,
    ) -> list[tuple[str, Path]]:
        labelled: list[tuple[str, Path]] = []
        for asset in project.get("assets", []):
            if asset_ids is not None and asset.get("id") not in asset_ids:
                continue
            if not asset.get("roles"):
                continue
            if (asset.get("media_type") or media_type(str(asset.get("filename", "")))) not in {"image", "character"}:
                continue
            labelled.append((str(asset["id"]), ensure_analysis_copy(self.store, project_id, asset)))
        return labelled

    def _h3_asset_images(
        self,
        project_id: str,
        project: dict[str, Any],
        segment: dict[str, Any],
    ) -> list[tuple[str, Path]]:
        assets = {str(asset.get("id")): asset for asset in project.get("assets", [])}
        labelled: list[tuple[str, Path]] = []
        picture_ids = picture_asset_ids(segment)
        for index, asset_id in enumerate(picture_ids, start=1):
            asset = assets.get(asset_id)
            if asset is not None and (asset.get("media_type") or media_type(str(asset.get("filename", "")))) in {"image", "character"}:
                labelled.append((f"Picture {index}", ensure_analysis_copy(self.store, project_id, asset)))
        binding = segment.get("template") or {}
        frame_references = () if segment.get("task_type") == "Ref2VA" else (
            ("First frame", binding.get("first_frame_asset_id")),
            ("Last frame", binding.get("last_frame_asset_id")),
            ("Scene reference", segment.get("scene_ref_asset_id")),
        )
        for label, asset_id in frame_references:
            if asset_id and str(asset_id) not in picture_ids and str(asset_id) in assets:
                asset = assets[str(asset_id)]
                if (asset.get("media_type") or media_type(str(asset.get("filename", "")))) in {"image", "character"}:
                    labelled.append((label, ensure_analysis_copy(self.store, project_id, asset)))
        return labelled

    async def suggest_reference_order(self, project_id: str, segment_id: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
        if segment is None:
            raise PlanningError(f"Unknown segment: {segment_id}")
        binding = segment.get("template") or {}
        slots = reference_slots(binding.get("tokens", []))
        if not slots:
            raise PlanningError("The assigned template has no [[REF_IMGn]] slots")
        assets = reference_candidates(project, segment)
        if not assets:
            raise PlanningError('No reference assets available')
        allowed_ids = [str(asset["id"]) for asset in assets]
        labelled_images = [
            (str(asset["id"]), ensure_analysis_copy(self.store, project_id, asset))
            for asset in assets
            if (asset.get("media_type") or media_type(str(asset.get("filename", "")))) in {"image", "character"}
        ]
        async with self.llama.operation(f"建議 {segment_id} 參考圖順序"):
            self.llama.update_progress(8, "分析參考圖片與分鏡")
            messages = attach_images(reference_order_messages(project, segment, slots, assets), labelled_images)
            result = await self.llama.json_completion(
                messages,
                reference_order_schema(allowed_ids, len(slots), flexible=True),
                mode="strict",
            )
            self.llama.update_progress(92, "驗證建議順序")
            image_order = validate_reference_order(result, allowed_ids, len(slots), flexible=True)
            try:
                resolve_references({**binding, 'image_order': image_order}, assets, require_complete=True)
            except ValueError as exc:
                raise PlanningError(str(exc)) from exc
        return {
            "slots": slots,
            "image_order": image_order,
            "rationale": str(result.get("rationale") or ""),
        }

    async def generate_blueprint(self, project_id: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        story = str(project["settings"].get("story", "")).strip()
        if not story:
            raise PlanningError("Story text is required")
        mode = str(project["settings"].get("expansion_mode", "balanced"))
        schema = blueprint_schema(
            require_dialogue=bool(project["settings"].get("create_dialogue")),
            require_narration=bool(project["settings"].get("create_narration")),
            dialogue_language=str(project["settings"].get("dialogue_language", "Chinese")),
            narration_language=str(project["settings"].get("narration_language", "Chinese")),
        )
        repair_errors: list[str] = []
        raw: dict[str, Any] | None = None
        blueprint: dict[str, Any] | None = None
        async with self.llama.operation("產生影片藍圖"):
            for attempt in range(3):
                self.llama.update_progress(5 + attempt * 30, f"生成藍圖 JSON（第 {attempt + 1}/3 輪）")
                messages = attach_images(
                    blueprint_messages(project, repair_errors or None, raw),
                    self._asset_images(project_id, project),
                )
                raw = await self.llama.json_completion(messages, schema, mode=mode)
                log_path = self.store.project_dir(project_id) / "logs" / f"blueprint_raw_{uuid.uuid4().hex[:10]}.json"
                log_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                self.llama.update_progress(22 + attempt * 30, "驗證並正規化分鏡")
                candidate = normalize_blueprint(raw, str(project["settings"].get("speech_speed", "normal")))
                repair_errors = validate_blueprint_content(
                    candidate,
                    require_dialogue=bool(project["settings"].get("create_dialogue")),
                    require_narration=bool(project["settings"].get("create_narration")),
                )
                if not repair_errors:
                    blueprint = candidate
                    break
            if blueprint is None:
                preview = "; ".join(repair_errors[:8])
                raise PlanningError(
                    "Muse-Glimmer returned an empty or invalid storyboard after 3 attempts. "
                    f"The previous storyboard was preserved. Errors: {preview}"
                )
            self.llama.update_progress(92, "儲存有效藍圖")
        total = sum(int(item["duration_seconds"]) for item in blueprint.get("segments", []))
        minimum = int(project["settings"]["minimum_duration_seconds"])
        if total < minimum:
            raise PlanningError(f"Blueprint is too short: {total}s; minimum is {minimum}s")
        maximum_normal = max(minimum * 1.10, minimum + 1)
        timing_conflicts: list[str] = []
        if total > maximum_normal:
            timing_conflicts.append(
                f"Blueprint is {total}s, above the normal +10% range ({maximum_normal:.1f}s). "
                "Approve a longer duration, shorten locked speech, or remove other visual beats."
            )
        for planned_segment in blueprint.get("segments", []):
            dialogue_seconds = round(
                sum(float(line.get("estimated_seconds", 0)) for line in planned_segment.get("dialogue", [])), 2
            )
            planned_segment["dialogue_estimated_seconds"] = dialogue_seconds
            if dialogue_seconds > float(planned_segment["duration_seconds"]):
                conflict = (
                    f"{planned_segment['id']} has {dialogue_seconds:.2f}s of locked speech in a "
                    f"{planned_segment['duration_seconds']}s segment."
                )
                planned_segment["timing_warning"] = conflict
                timing_conflicts.append(conflict)
        if len(blueprint.get("segments", [])) > 100:
            raise PlanningError("Blueprint exceeds the 100-segment limit")
        project["blueprint"] = {
            key: value for key, value in blueprint.items() if key != "segments"
        }
        project["blueprint"]["planned_duration_seconds"] = total
        project["blueprint"]["timing_conflicts"] = timing_conflicts
        project["segments"] = blueprint["segments"]
        project["blueprint_status"] = "needs_timing_review" if timing_conflicts else "ready"
        self.store.save(project)
        return project

    async def generate_segment_prompt(self, project_id: str, segment_id: str) -> dict[str, Any]:
        async with self.llama.operation(f"產生 {segment_id} prompt"):
            return await self._generate_segment_prompt(project_id, segment_id, 0, 100)

    async def optimize_segment_prompt(
        self, project_id: str, segment_id: str, current_prompt: str,
        reference_selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not current_prompt.strip():
            raise PlanningError("請先輸入目前的 prompt")
        async with self.llama.operation(f"修改/優化 {segment_id} prompt"):
            project = self.store.load(project_id)
            segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
            if segment is None:
                raise PlanningError(f"Unknown segment: {segment_id}")
            if reference_selection is not None and segment.get("template"):
                known_ids = {str(asset.get("id")) for asset in project.get("assets", [])}
                order = [str(asset_id) for asset_id in reference_selection.get("image_order", []) if asset_id]
                frame_ids = {
                    key: str(reference_selection[key]) if reference_selection.get(key) else None
                    for key in ("first_frame_asset_id", "last_frame_asset_id", "scene_ref_asset_id")
                }
                selected = [*order, *(asset_id for asset_id in frame_ids.values() if asset_id)]
                if any(asset_id not in known_ids for asset_id in selected):
                    raise PlanningError("選用素材不存在，請重新選擇")
                binding = {**segment["template"], "image_order": order,
                           "first_frame_asset_id": frame_ids["first_frame_asset_id"],
                           "last_frame_asset_id": frame_ids["last_frame_asset_id"]}
                try:
                    bind_references(binding, project["assets"], require_complete=False)
                except ValueError as exc:
                    raise PlanningError(str(exc)) from exc
                segment["template"] = binding
                segment["scene_ref_asset_id"] = frame_ids["scene_ref_asset_id"]
            segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
            mode = str(segment.get("expansion_mode") or project["settings"].get("expansion_mode", "balanced"))
            prompt = current_prompt.strip()
            # Optimization uses the edited prompt as its only text source. Keep task
            # metadata and reference bindings for H3 format checks, but exclude the
            # stored storyboard and its locked dialogue from this path.
            format_segment = {
                "task_type": segment.get("task_type") or "T2VA",
                "duration_seconds": segment["duration_seconds"],
                "template": segment.get("template") or {},
                "dialogue": [],
            }
            skill_project = {
                "settings": {"aspect_ratio": project["settings"].get("aspect_ratio")},
                "assets": project.get("assets", []),
            }
            errors: list[str] = []
            for attempt in range(3):
                self.llama.update_progress(5 + attempt * 30, f"優化 {segment_id} prompt（第 {attempt + 1}/3 輪）")
                system = h3_messages(skill_project, format_segment, repair_errors=errors or None)[0]["content"]
                user = {
                    "current_prompt": prompt,
                    "reference_assets": picture_reference_context(project, segment),
                    "reference_audio_video": media_reference_context(project, segment),
                }
                if errors:
                    user["validation_errors_to_repair"] = errors
                messages = attach_images(
                    [
                        {"role": "system", "content": system + "\nImprove and format the current prompt while preserving its meaning. Use only the current prompt and selected reference assets as source material. Spoken text in current_prompt is the complete dialogue source; do not infer dialogue from any stored storyboard or segment data."},
                        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                    ],
                    self._h3_asset_images(project_id, project, segment),
                )
                prompt = normalize_h3_format(to_simplified(await self.llama.completion(messages, mode=mode)))
                self.llama.update_progress(24 + attempt * 30, f"驗證 {segment_id} H3 格式")
                errors = validate_h3_prompt(prompt, format_segment)
                if not errors:
                    break
            segment["prompt"] = prompt
            segment["prompt_status"] = "ready" if not errors else "invalid"
            segment["prompt_validation_errors"] = errors
            segment["prompt_validation_warnings"] = h3_prompt_warnings(prompt, segment)
            segment["prompt_source"] = "llm"
            self.store.save(project)
            return segment

    async def _generate_segment_prompt(
        self,
        project_id: str,
        segment_id: str,
        progress_base: float,
        progress_span: float,
    ) -> dict[str, Any]:
        project = self.store.load(project_id)
        segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
        if segment is None:
            raise PlanningError(f"Unknown segment: {segment_id}")
        segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
        messages = attach_images(
            h3_messages(project, segment),
            self._h3_asset_images(project_id, project, segment),
        )
        mode = str(segment.get("expansion_mode") or project["settings"].get("expansion_mode", "balanced"))
        prompt = ""
        errors: list[str] = []
        for attempt in range(3):
            self.llama.update_progress(
                progress_base + progress_span * (0.05 + attempt * 0.30),
                f"生成 {segment_id} prompt（第 {attempt + 1}/3 輪）",
            )
            if attempt:
                messages = attach_images(
                    h3_messages(project, segment, repair_errors=errors),
                    self._h3_asset_images(project_id, project, segment),
                )
            prompt = normalize_h3_format(to_simplified(await self.llama.completion(messages, mode=mode)))
            self.llama.update_progress(
                progress_base + progress_span * (0.24 + attempt * 0.30),
                f"驗證 {segment_id} H3 格式",
            )
            errors = validate_h3_prompt(prompt, segment)
            if not errors:
                break
        segment["prompt"] = prompt
        segment["prompt_status"] = "ready" if not errors else "invalid"
        segment["prompt_validation_errors"] = errors
        segment["prompt_validation_warnings"] = h3_prompt_warnings(prompt, segment)
        segment["prompt_source"] = "llm"
        self.store.save(project)
        return segment

    async def generate_all_prompts(self, project_id: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        segments = list(project.get("segments", []))
        async with self.llama.operation("依序產生全部 prompts"):
            count = max(1, len(segments))
            for index, segment in enumerate(segments):
                await self._generate_segment_prompt(
                    project_id,
                    segment["id"],
                    index * 100 / count,
                    100 / count,
                )
        return self.store.load(project_id)
