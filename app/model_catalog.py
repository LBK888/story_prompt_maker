from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from .config import ROOT


PROVIDER_LABELS = {
    "local": "本機 GGUF",
    "ollama": "Ollama API",
    "openai": "OpenAI API",
    "openrouter": "OpenRouter API",
    "custom": "OpenAI 相容 API",
}


class ModelCatalog:
    """Keeps the cheap-to-render model inventory used by the settings dialog."""

    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.local_models: list[dict[str, Any]] = []
        self.mmproj_models: list[dict[str, Any]] = []

    def scan(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        project_root = self.root.resolve()
        excluded = {
            ".git", ".venv", ".pytest_cache", "__pycache__", "logs", "projects",
            "runtime", "static", "templates", "tests", "workflow_templates",
        }
        for current, directories, files in os.walk(project_root):
            directories[:] = [name for name in directories if name not in excluded]
            for filename in files:
                if not filename.lower().endswith(".gguf"):
                    continue
                path = Path(current) / filename
                try:
                    relative = path.relative_to(project_root).as_posix()
                    size = path.stat().st_size
                except OSError:
                    continue
                models.append({"name": filename, "path": relative, "size_bytes": size})
        models.sort(key=lambda item: item["path"].lower())
        self.mmproj_models = [item for item in models if "mmproj" in item["name"].lower()]
        self.local_models = [item for item in models if "mmproj" not in item["name"].lower()]
        return self.local_models


def validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError("API URL 必須以 http:// 或 https:// 開頭")
    return url


async def fetch_remote_models(
    provider: str,
    base_url: str,
    api_key: str = "",
) -> list[str]:
    base_url = validate_base_url(base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    timeout = httpx.Timeout(30, connect=10)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if provider == "ollama":
                response = await client.get(f"{base_url}/api/tags", headers=headers)
            else:
                response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"無法取得模型清單：{exc}") from exc

    if provider == "ollama":
        values = [item.get("name") or item.get("model") for item in payload.get("models", [])]
    else:
        values = [item.get("id") for item in payload.get("data", [])]
    return sorted({str(value).strip() for value in values if value}, key=str.lower)
