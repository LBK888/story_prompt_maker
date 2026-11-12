from __future__ import annotations

import mimetypes
from pathlib import Path


IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"})
SUPPORTED_MEDIA = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

MEDIA_SIZE_LIMITS = {
    "image": 50 * 1024 * 1024,
    "video": 500 * 1024 * 1024,
    "audio": 200 * 1024 * 1024,
}


def media_type(filename: str | Path) -> str | None:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    return None


def media_mime_type(filename: str | Path) -> str:
    return mimetypes.guess_type(str(filename))[0] or "application/octet-stream"


def media_size_limit(filename: str | Path) -> int:
    kind = media_type(filename)
    return MEDIA_SIZE_LIMITS.get(kind or "", 0)
