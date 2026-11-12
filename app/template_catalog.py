from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .storage import ProjectStore
from .reference_order import bound_reference_asset_ids, bind_references
from .template_engine import WorkflowTemplate, load_template, scan_templates


class TemplateCatalog:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def list(self) -> tuple[list[WorkflowTemplate], dict[str, str]]:
        return scan_templates()

    def get(self, name: str) -> WorkflowTemplate:
        valid, _ = self.list()
        template = next((item for item in valid if item.name == name), None)
        if template is None:
            raise FileNotFoundError(f"Unknown or invalid workflow template: {name}")
        return template

    def assign(
        self,
        project_id: str,
        segment_id: str,
        template_name: str,
        task_type: str | None = None,
    ) -> dict[str, Any]:
        template = self.get(template_name)
        project = self.store.load(project_id)
        segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
        if segment is None:
            raise KeyError(segment_id)
        snapshot_dir = self.store.project_dir(project_id) / "template_snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / f"{template.sha256}.json"
        if not snapshot.exists():
            snapshot.write_text(
                json.dumps(template.content, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if template.note:
            snapshot.with_suffix(".note.txt").write_text(template.note + "\n", encoding="utf-8")
        previous = segment.get("template") or {}
        if task_type is not None and segment.get("task_type") != task_type:
            segment["task_type"] = task_type
            segment["prompt_status"] = "stale"
        segment["template"] = {
            "name": template.name,
            "source_sha256": template.sha256,
            "snapshot": str(snapshot.relative_to(self.store.project_dir(project_id))).replace("\\", "/"),
            "tokens": list(template.tokens),
            "note": template.note,
            "image_order": previous.get("image_order", []),
            "first_frame_asset_id": previous.get("first_frame_asset_id") or segment.get("generated_image_assets", {}).get("first_frame"),
            "last_frame_asset_id": previous.get("last_frame_asset_id") or segment.get("generated_image_assets", {}).get("last_frame"),
            "source_update_available": False,
        }
        # Retain compatible selections when switching templates.
        selected = []
        for asset_id in previous.get('image_order', []):
            trial = {**segment['template'], 'image_order': [*selected, asset_id]}
            try:
                bind_references(trial, project['assets'])
                selected.append(asset_id)
            except ValueError:
                continue
        segment['template']['image_order'] = selected
        bind_references(segment['template'], project['assets'])
        segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
        self.store.save(project)
        return segment

    def assign_all(self, project_id: str, template_name: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        for segment in list(project.get("segments", [])):
            self.assign(project_id, segment["id"], template_name)
        return self.store.load(project_id)

    def load_snapshot(self, project_id: str, segment: dict[str, Any]) -> WorkflowTemplate:
        binding = segment.get("template")
        if not binding:
            raise ValueError("Segment has no workflow template")
        snapshot = self.store.project_dir(project_id) / binding["snapshot"]
        return load_template(snapshot)

    def check_updates(self, project_id: str) -> dict[str, Any]:
        project = self.store.load(project_id)
        valid, _ = self.list()
        current = {item.name: item.sha256 for item in valid}
        changed = False
        for segment in project.get("segments", []):
            binding = segment.get("template")
            if not binding:
                continue
            available = current.get(binding["name"])
            update = bool(available and available != binding["source_sha256"])
            if binding.get("source_update_available") != update:
                binding["source_update_available"] = update
                changed = True
        if changed:
            self.store.save(project)
        return project
