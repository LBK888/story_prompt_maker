from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from threading import RLock
from typing import Any

from .config import ROOT
from .domain import PROJECT_SCHEMA_VERSION, new_event, new_project, utc_now
from .media_types import media_mime_type, media_type


PROJECTS_DIR = ROOT / "projects"
TEMPLATES_DIR = ROOT / "workflow_templates"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ProjectNotFoundError(FileNotFoundError):
    pass


class ProjectStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir if base_dir is not None else Path(os.environ.get("VID_AUTO_PROMPT_PROJECTS_DIR", str(PROJECTS_DIR)))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def project_dir(self, project_id: str) -> Path:
        if not SAFE_ID.fullmatch(project_id):
            raise ValueError("Invalid project id")
        return self.base_dir / project_id

    def project_file(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def _ensure_layout(self, project_id: str) -> None:
        root = self.project_dir(project_id)
        for relative in (
            "ref_img",
            "ref_img/.analysis",
            "submitted_workflows",
            "segments",
            "extracted_frames",
            "logs",
            "final",
            "template_snapshots",
        ):
            (root / relative).mkdir(parents=True, exist_ok=True)

    def create(self, name: str, story: str = "") -> dict[str, Any]:
        project = new_project(name, story)
        with self._lock:
            self._ensure_layout(project["id"])
            self.save(project)
        return project

    def list(self, include_archived: bool = False) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for file in self.base_dir.glob("*/project.json"):
            try:
                item = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if include_archived or not item.get("archived", False):
                projects.append(item)
        return sorted(projects, key=lambda item: item.get("updated_at", ""), reverse=True)

    def load(self, project_id: str) -> dict[str, Any]:
        file = self.project_file(project_id)
        if not file.exists():
            raise ProjectNotFoundError(project_id)
        try:
            project = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Project file is damaged: {file}: {exc}") from exc
        if project.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported project schema {project.get('schema_version')}; "
                f"expected {PROJECT_SCHEMA_VERSION}"
            )
        from .character_assets import migrate_characters
        changed = migrate_characters(project)
        for asset in project.get("assets", []):
            inferred_type = "character" if asset.get("media_type") == "character" else media_type(str(asset.get("filename") or asset.get("original_name") or ""))
            if asset.get("media_type") != inferred_type:
                asset["media_type"] = inferred_type
                changed = True
            inferred_mime = media_mime_type(str(asset.get("filename") or asset.get("original_name") or ""))
            if asset.get("mime_type") != inferred_mime:
                asset["mime_type"] = inferred_mime
                changed = True
        from .reference_order import bind_references
        for segment in project.get('segments', []):
            binding = segment.get('template')
            if binding and 'tokens' in binding:
                before_mapping = binding.get('reference_mapping')
                try:
                    bind_references(binding, project.get('assets', []))
                except ValueError:
                    binding['reference_mapping'] = {}
                changed = changed or before_mapping != binding['reference_mapping']
        if project.get("segments"):
            from .prompt_policy import h3_prompt_warnings, normalize_blueprint, normalize_h3_format, validate_h3_prompt

            before = json.dumps(project.get("segments"), ensure_ascii=False, sort_keys=True)
            normalize_blueprint(
                {
                    "task_type": (project.get("blueprint") or {}).get("task_type", "T2VA"),
                    "segments": project["segments"],
                },
                str((project.get("settings") or {}).get("speech_speed", "normal")),
                preserve_blank_summary=(project.get("blueprint") or {}).get("creation_mode") == "manual_blank",
            )
            for segment in project["segments"]:
                if segment.get("prompt_source") == "manual":
                    segment["prompt_validation_errors"] = []
                    segment["prompt_validation_warnings"] = []
                    if segment.get("prompt_status") in {"missing", "invalid"}:
                        segment["prompt_status"] = "ready"
                    continue
                if str(segment.get("prompt") or "").strip():
                    segment["prompt"] = normalize_h3_format(str(segment["prompt"]))
                    errors = validate_h3_prompt(segment["prompt"], segment)
                    segment["prompt_validation_errors"] = errors
                    segment["prompt_validation_warnings"] = h3_prompt_warnings(segment["prompt"], segment)
                    if segment.get("prompt_status") != "stale":
                        segment["prompt_status"] = "ready" if not errors else "invalid"
            after = json.dumps(project.get("segments"), ensure_ascii=False, sort_keys=True)
            changed = changed or before != after
        if changed:
            self.save(project)
        return project

    def save(self, project: dict[str, Any], *, expected_revision: int | None = None) -> None:
        project_id = str(project.get("id", ""))
        self._ensure_layout(project_id)
        project["updated_at"] = utc_now()
        target = self.project_file(project_id)
        temporary = target.with_suffix(".json.tmp")
        with self._lock:
            previous = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            revision = int(previous.get("revision", 0))
            if expected_revision is not None and revision != expected_revision:
                raise ValueError(f"Project revision changed: expected {expected_revision}, current {revision}")
            project["revision"] = revision + 1
            payload = json.dumps(project, ensure_ascii=False, indent=2) + "\n"
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, target)

    def mutate(self, project_id: str, mutation: Any) -> dict[str, Any]:
        with self._lock:
            project = self.load(project_id)
            mutation(project)
            self.save(project)
            return project

    def add_event(self, project_id: str, kind: str, message: str, **details: Any) -> None:
        def mutation(project: dict[str, Any]) -> None:
            events = project.setdefault("events", [])
            events.append(new_event(kind, message, **details))
            if len(events) > 500:
                del events[:-500]

        self.mutate(project_id, mutation)

    def import_asset(self, project_id: str, source: Path, original_name: str, *, preserve_name: bool = False) -> dict[str, Any]:
        project = self.load(project_id)
        digest = sha256_file(source)
        suffix = Path(original_name).suffix.lower() or source.suffix.lower()
        safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(original_name).stem).strip("_") or "media"
        existing = next((item for item in project["assets"] if item.get("sha256") == digest), None)
        if existing:
            return existing
        if preserve_name:
            if Path(original_name).name != original_name or re.search(r'[\\/:*?"<>|\x00-\x1f]', original_name):
                raise ValueError("Invalid generated image filename")
            filename = original_name
            stem = Path(filename).stem
            counter = 1
            while (self.project_dir(project_id) / "ref_img" / filename).exists():
                filename = f"{stem}({counter}){suffix}"
                counter += 1
        else:
            filename = f"{safe_stem[:48]}_{digest[:12]}{suffix}"
        destination = self.project_dir(project_id) / "ref_img" / filename
        if not destination.exists():
            shutil.copy2(source, destination)
        asset = {
            "id": f"asset_{digest[:12]}",
            "original_name": original_name,
            "filename": filename,
            "sha256": digest,
            "media_type": media_type(original_name),
            "mime_type": media_mime_type(original_name),
            "roles": [],
            "bindings": [],
            "notes": "",
            "character_name": "",
            "created_at": utc_now(),
            "missing": False,
        }
        project["assets"].append(asset)
        self.save(project)
        return asset

    def delete_asset(self, project_id: str, asset_id: str) -> dict[str, Any]:
        """Delete one asset and clear every project reference to it."""
        project = self.load(project_id)
        asset = next((item for item in project.get("assets", []) if item.get("id") == asset_id), None)
        if asset is None:
            raise KeyError(asset_id)

        ref_root = (self.project_dir(project_id) / "ref_img").resolve()
        stored_path = (ref_root / str(asset.get("filename", ""))).resolve()
        if stored_path.parent != ref_root:
            raise ValueError("Invalid stored asset path")
        filenames = [asset.get(key) for key in ('filename', 'audio_filename', 'headshot_filename') if asset.get(key)]
        for filename in filenames:
            path = (ref_root / filename).resolve()
            if path.parent != ref_root:
                raise ValueError('Invalid stored asset path')
        for filename in filenames:
            path = ref_root / filename
            shared = any(other['id'] != asset_id and filename in [other.get(k) for k in ('filename', 'audio_filename', 'headshot_filename')] for other in project['assets'])
            if path.is_file() and not shared:
                path.unlink()

        digest = str(asset.get("sha256", ""))
        if digest:
            analysis_root = (ref_root / ".analysis").resolve()
            for suffix in (".jpg", ".png"):
                analysis_path = (analysis_root / f"{digest[:16]}{suffix}").resolve()
                if analysis_path.parent == analysis_root and analysis_path.is_file():
                    analysis_path.unlink()

        from .reference_order import bound_reference_asset_ids

        for segment in project.get("segments", []):
            binding = segment.get("template") or {}
            was_bound = asset_id in bound_reference_asset_ids(segment)
            for key in ("first_frame_asset_id", "last_frame_asset_id"):
                if binding.get(key) == asset_id:
                    binding[key] = None
            if segment.get("scene_ref_asset_id") == asset_id:
                segment["scene_ref_asset_id"] = None
            for key, value in list(segment.get("generated_image_assets", {}).items()):
                if value == asset_id:
                    del segment["generated_image_assets"][key]
            if "image_order" in binding:
                binding["image_order"] = [None if item == asset_id else item for item in binding["image_order"]]
            segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
            if was_bound and str(segment.get("prompt") or "").strip():
                segment["prompt_status"] = "stale"
            for candidate in segment.get("image_candidates", []):
                if candidate.get("adopted_asset_id") == asset_id:
                    candidate["adopted_asset_id"] = None
                    if segment.get("selected_image_candidate_ids", {}).get(candidate.get("kind")) == candidate.get("id"):
                        segment["selected_image_candidate_ids"].pop(candidate["kind"], None)

        project["assets"] = [item for item in project.get("assets", []) if item.get("id") != asset_id]
        from .reference_order import bind_references
        for segment in project.get('segments', []):
            binding = segment.get('template')
            if binding and 'tokens' in binding:
                try:
                    bind_references(binding, project['assets'])
                except ValueError:
                    binding['reference_mapping'] = {}
        self.save(project)
        return {"deleted": asset, "project": project}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
