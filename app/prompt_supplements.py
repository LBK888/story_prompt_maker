from __future__ import annotations

from functools import lru_cache
from pathlib import Path


RUNTIME_PROMPT_START = "<!-- runtime-prompt:start -->"
RUNTIME_PROMPT_END = "<!-- runtime-prompt:end -->"

_SUPPLEMENT_FILES = {
    "T2VA": "MiniMax H3 T2VA Storytelling Supplement Skill.md",
    "Ref2VA": "MiniMax H3 Ref2VA Storytelling Supplement Skill.md",
}


@lru_cache(maxsize=None)
def storytelling_supplement(task_type: str) -> str:
    """Load only the compact runtime contract from a storytelling skill file."""
    filename = _SUPPLEMENT_FILES.get(task_type)
    if not filename:
        return ""

    path = Path(__file__).resolve().parent.parent / "skills" / filename
    text = path.read_text(encoding="utf-8")
    try:
        runtime = text.split(RUNTIME_PROMPT_START, 1)[1].split(RUNTIME_PROMPT_END, 1)[0]
    except IndexError as exc:
        raise RuntimeError(f"Storytelling supplement has no runtime prompt block: {path}") from exc
    return runtime.strip()
