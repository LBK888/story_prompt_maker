from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import TEMPLATES_DIR


TOKEN_PATTERN = re.compile(r"^\[\[([A-Za-z][A-Za-z0-9_]*)\]\]$")
TOKEN_LIKE_PATTERN = re.compile(r"\[\[[A-Za-z0-9_]+\]\]")
REF_TOKEN_PATTERN = re.compile(r"^REF_(?:IMG|AUD|VID)(0|[1-9][0-9]*)$")
KNOWN_TOKENS = {
    "PROMPT",
    "SEED",
    "DURATION",
    "WIDTH",
    "HEIGHT",
    "ASPECT_RATIO",
    "FIRST_FRAME",
    "LAST_FRAME",
    "OUTPUT_PREFIX",
    "TASK_TYPE",
    "PROJECT_ID",
    "SEGMENT_ID",
    "Drive_AUD",
}


class TemplateValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowTemplate:
    name: str
    path: Path
    sha256: str
    content: dict[str, Any]
    tokens: tuple[str, ...]
    note: str = ""


def _walk(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    else:
        yield value


def find_tokens(document: Any) -> list[str]:
    tokens: list[str] = []
    for value in _walk(document):
        if isinstance(value, str):
            match = TOKEN_PATTERN.fullmatch(value)
            if match:
                tokens.append(match.group(1))
            elif TOKEN_LIKE_PATTERN.search(value):
                raise TemplateValidationError(
                    f"Placeholder must be the entire JSON value, not part of a string: {value!r}"
                )
    return tokens


def validate_template(document: Any) -> tuple[str, ...]:
    if not isinstance(document, dict):
        raise TemplateValidationError("Workflow template root must be a JSON object")
    tokens = find_tokens(document)
    counts = Counter(tokens)
    unknown = sorted(
        token for token in tokens if token not in KNOWN_TOKENS and not REF_TOKEN_PATTERN.fullmatch(token)
    )
    if unknown:
        raise TemplateValidationError("Unknown placeholders: " + ", ".join(unknown))
    if not counts.get("OUTPUT_PREFIX"):
        raise TemplateValidationError("[[OUTPUT_PREFIX]] must occur at least once")
    return tuple(dict.fromkeys(tokens))


def load_template(path: Path) -> WorkflowTemplate:
    raw = path.read_bytes()
    try:
        content = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateValidationError(f"Invalid UTF-8 JSON in {path.name}: {exc}") from exc
    tokens = validate_template(content)
    # The note is a sidecar, never a workflow node or a value sent to ComfyUI.
    note_path = path.with_suffix(".note.txt")
    try:
        note = note_path.read_text(encoding="utf-8-sig").strip() if note_path.exists() else ""
    except (OSError, UnicodeDecodeError):
        note = ""
    digest = hashlib.sha256(raw if not note else raw + b"\0VAP-NOTE\0" + note.encode("utf-8")).hexdigest()
    return WorkflowTemplate(
        name=path.stem,
        path=path,
        sha256=digest,
        content=content,
        tokens=tokens,
        note=note,
    )


def scan_templates(directory: Path = TEMPLATES_DIR) -> tuple[list[WorkflowTemplate], dict[str, str]]:
    directory.mkdir(parents=True, exist_ok=True)
    valid: list[WorkflowTemplate] = []
    invalid: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            valid.append(load_template(path))
        except (OSError, TemplateValidationError) as exc:
            invalid[path.name] = str(exc)
    return valid, invalid


def _replace(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _replace(child, replacements) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace(child, replacements) for child in value]
    if isinstance(value, str):
        match = TOKEN_PATTERN.fullmatch(value)
        if match:
            token = match.group(1)
            if token not in replacements:
                raise TemplateValidationError(f"No replacement value for [[{token}]]")
            replacement = replacements[token]
            if replacement is None or (replacement == "" and token != "PROMPT"):
                raise TemplateValidationError(f"Replacement for [[{token}]] is empty")
            return replacement
    return value


def render_template(template: WorkflowTemplate, replacements: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(set(template.tokens) - set(replacements))
    if missing:
        raise TemplateValidationError("Missing replacements: " + ", ".join(missing))
    rendered = _replace(deepcopy(template.content), replacements)
    payload = json.dumps(rendered, ensure_ascii=False, separators=(",", ":"))
    if TOKEN_LIKE_PATTERN.search(payload):
        raise TemplateValidationError("Rendered workflow still contains placeholders")
    reparsed = json.loads(payload)
    return reparsed
