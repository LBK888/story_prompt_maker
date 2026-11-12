from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_prompt_templates(directory: Path) -> dict[str, Any]:
    templates: list[dict[str, str]] = []
    errors: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else data.get("templates")
            if not isinstance(entries, list):
                raise ValueError("頂層必須是陣列或包含 templates 陣列")
            for index, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict):
                    raise ValueError(f"第 {index} 筆必須是物件")
                name = entry.get("Name")
                kind = entry.get("Type")
                note = entry.get("Note", "")
                prompt = entry.get("Prompt")
                if not all(isinstance(value, str) for value in (name, kind, note, prompt)):
                    raise ValueError(f"第 {index} 筆欄位必須是文字")
                if not name.strip() or kind not in {"整段", "片段"} or not prompt.strip():
                    raise ValueError(f"第 {index} 筆需有 Name、Type（整段/片段）與 Prompt")
                templates.append({"Name": name, "Type": kind, "Note": note, "Prompt": prompt})
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            errors.append(f"{path.name}: {exc}")
    return {"templates": templates, "errors": errors}
