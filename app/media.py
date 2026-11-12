from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .media_types import IMAGE_EXTENSIONS, SUPPORTED_MEDIA
from .storage import ProjectStore, sha256_file


SUPPORTED_IMAGES = IMAGE_EXTENSIONS


def create_analysis_copy(source: Path, destination: Path, max_edge: int = 2048) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        if has_alpha:
            output = destination.with_suffix(".png")
            image.convert("RGBA").save(output, format="PNG", optimize=True)
        else:
            output = destination.with_suffix(".jpg")
            image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
    return output


def ensure_analysis_copy(store: ProjectStore, project_id: str, asset: dict[str, Any]) -> Path:
    source = store.project_dir(project_id) / "ref_img" / asset["filename"]
    if not source.is_file():
        raise FileNotFoundError(source)
    directory = store.project_dir(project_id) / "ref_img" / ".analysis"
    stem = asset["sha256"][:16]
    for suffix in (".jpg", ".png"):
        candidate = directory / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return create_analysis_copy(source, directory / stem)


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def attach_images(
    messages: list[dict[str, Any]],
    labelled_paths: list[tuple[str, Path]],
) -> list[dict[str, Any]]:
    if not labelled_paths:
        return messages
    cloned = [dict(message) for message in messages]
    user = cloned[-1]
    original = user.get("content", "")
    parts: list[dict[str, Any]] = [{"type": "text", "text": str(original)}]
    for label, path in labelled_paths:
        parts.append({"type": "text", "text": f"Reference asset {label}; obey its user-assigned role only."})
        parts.append({"type": "image_url", "image_url": {"url": data_url(path)}})
    user["content"] = parts
    return cloned


def scan_ref_img(store: ProjectStore, project_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    project = store.load(project_id)
    directory = store.project_dir(project_id) / "ref_img"
    known = {item.get("sha256"): item for item in project.get("assets", [])}
    component_files = {a.get(k) for a in project.get("assets", []) for k in ("filename", "audio_filename", "headshot_filename")}
    additions: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name in component_files or not path.is_file() or path.suffix.lower() not in SUPPORTED_MEDIA:
            continue
        try:
            digest = sha256_file(path)
            if digest in known:
                continue
            additions.append(store.import_asset(project_id, path, path.name))
            known[digest] = additions[-1]
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return additions, errors


def safe_remote_filename(project_id: str, asset: dict[str, Any]) -> str:
    suffix = Path(asset["filename"]).suffix.lower()
    return f"vid_auto_prompt/{project_id}/{asset['id']}_{asset['sha256'][:12]}{suffix}"
