from __future__ import annotations

import asyncio
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


class ComfyUIError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ComfyOutput:
    filename: str
    subfolder: str
    output_type: str
    media_type: str

    @property
    def relative_name(self) -> str:
        return f"{self.subfolder}/{self.filename}".strip("/")


class ComfyClient:
    def __init__(self, base_url: str, timeout_seconds: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=10)
        self.client_id = f"vid-auto-prompt-{uuid4().hex}"

    async def health(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/system_stats")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyUIError(f"Cannot reach ComfyUI at {self.base_url}: {exc}") from exc

    async def upload_input(self, path: Path, remote_name: str, overwrite: bool = True) -> dict[str, Any]:
        parent = Path(remote_name).parent.as_posix()
        if parent == ".":
            parent = ""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as client:
                with path.open("rb") as stream:
                    response = await client.post(
                        f"{self.base_url}/upload/image",
                        files={
                            "image": (
                                Path(remote_name).name,
                                stream,
                                mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                            )
                        },
                        data={
                            "type": "input",
                            "subfolder": parent,
                            "overwrite": "true" if overwrite else "false",
                        },
                    )
                response.raise_for_status()
                result = response.json()
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise ComfyUIError(f"Failed to upload {path.name}: {exc}") from exc
        if not isinstance(result, dict) or not result.get("name"):
            raise ComfyUIError(f"ComfyUI returned an invalid upload result for {path.name}")
        return result

    async def upload_image(self, path: Path, remote_name: str, overwrite: bool = True) -> dict[str, Any]:
        """Backward-compatible alias for callers that upload continuity frames."""
        return await self.upload_input(path, remote_name, overwrite)

    async def submit(self, workflow: dict[str, Any]) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": workflow, "client_id": self.client_id},
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"; response: {exc.response.text[:2000]}"
            raise ComfyUIError(f"ComfyUI rejected the workflow: {exc}{detail}") from exc
        prompt_id = result.get("prompt_id") if isinstance(result, dict) else None
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI response did not include prompt_id: {result!r}")
        return str(prompt_id)

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyUIError(f"Cannot read ComfyUI history for {prompt_id}: {exc}") from exc
        if not isinstance(data, dict):
            return None
        item = data.get(prompt_id)
        return item if isinstance(item, dict) else None

    async def queue_state(self, prompt_id: str) -> tuple[str, int | None]:
        """Locate one prompt in ComfyUI's running or pending queue."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/queue")
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ComfyUIError(f"Cannot read ComfyUI queue for {prompt_id}: {exc}") from exc
        if not isinstance(data, dict):
            raise ComfyUIError("ComfyUI returned an invalid queue response")
        for key, state in (("queue_running", "running"), ("queue_pending", "queued")):
            entries = data.get(key, [])
            if not isinstance(entries, list):
                continue
            for position, item in enumerate(entries, start=1):
                if isinstance(item, list) and len(item) > 1 and str(item[1]) == prompt_id:
                    return state, position if state == "queued" else None
        return "unknown", None

    async def delete_queued(self, prompt_id: str) -> None:
        """Remove one pending prompt without clearing other ComfyUI jobs."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/queue", json={"delete": [prompt_id]})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"Cannot cancel ComfyUI prompt {prompt_id}: {exc}") from exc

    async def free_memory(self) -> None:
        """Ask ComfyUI to unload models and reclaim unused device memory."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/free",
                    json={"unload_models": True, "free_memory": True},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyUIError(f"Cannot release ComfyUI model memory: {exc}") from exc

    async def wait(self, prompt_id: str, timeout_seconds: int = 7200, poll_seconds: float = 2) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            item = await self.history(prompt_id)
            if item:
                status = item.get("status", {})
                status_text = str(status.get("status_str", "")) if isinstance(status, dict) else ""
                completed = bool(status.get("completed")) if isinstance(status, dict) else False
                if status_text == "error":
                    messages = status.get("messages", []) if isinstance(status, dict) else []
                    raise ComfyUIError(f"ComfyUI workflow failed: {messages!r}")
                if completed or item.get("outputs"):
                    return item
            await asyncio.sleep(poll_seconds)
        raise ComfyUIError(f"Timed out waiting for ComfyUI prompt {prompt_id}")

    @staticmethod
    def outputs(history_item: dict[str, Any]) -> list[ComfyOutput]:
        found: list[ComfyOutput] = []
        outputs = history_item.get("outputs", {})
        if not isinstance(outputs, dict):
            return found
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for media_type in ("videos", "gifs", "images", "audio"):
                values = node_output.get(media_type, [])
                if not isinstance(values, list):
                    continue
                for item in values:
                    if isinstance(item, dict) and item.get("filename"):
                        found.append(
                            ComfyOutput(
                                filename=str(item["filename"]),
                                subfolder=str(item.get("subfolder", "")),
                                output_type=str(item.get("type", "output")),
                                media_type=media_type,
                            )
                        )
        return found

    @staticmethod
    def matching_outputs(outputs: list[ComfyOutput], output_prefix: str) -> list[ComfyOutput]:
        normalized = output_prefix.replace("\\", "/").strip("/")
        basename = Path(normalized).name
        matches: list[ComfyOutput] = []
        for output in outputs:
            relative = output.relative_name.replace("\\", "/")
            if relative.startswith(normalized) or output.filename.startswith(basename):
                if output.media_type in {"videos", "gifs"} or output.filename.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                    matches.append(output)
        return matches

    async def download(self, output: ComfyOutput, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
        params = {"filename": output.filename, "subfolder": output.subfolder, "type": output.output_type}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
                async with client.stream("GET", f"{self.base_url}/view", params=params) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            stream.write(chunk)
            if temporary.stat().st_size == 0:
                raise ComfyUIError(f"ComfyUI returned an empty file for {output.relative_name}")
            temporary.replace(destination)
        except (OSError, httpx.HTTPError) as exc:
            raise ComfyUIError(f"Failed to download {output.relative_name}: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return destination
