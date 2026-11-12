"""The shared HTTP adapter used by the CLI and MCP process."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx


class AgentClientError(RuntimeError):
    def __init__(self, detail: Any, status: int | None = None):
        self.detail, self.status = detail, status
        super().__init__(json.dumps({"status": status, "detail": detail}, ensure_ascii=False))


class AgentClient:
    def __init__(self, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or os.environ.get("VID_AUTO_PROMPT_URL", "http://127.0.0.1:7865")).rstrip("/")
        self.transport = transport

    async def response(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport,
                                         timeout=httpx.Timeout(60, connect=5), trust_env=False) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AgentClientError({"code": "backend_unavailable", "message":
                                   f"Cannot reach {self.base_url}. Start run.py --mode agent. {exc}"}) from exc
        if response.is_error:
            try:
                detail = response.json().get("detail", response.json())
            except ValueError:
                detail = response.text
            raise AgentClientError(detail, response.status_code)
        return response

    async def request(self, method: str, path: str, **kwargs) -> Any:
        return (await self.response(method, path, **kwargs)).json()

    async def context(self, project_id: str, task: str, **kwargs) -> dict[str, Any]:
        return await self.request("POST", f"/api/agent/projects/{quote(project_id, safe='')}/context",
                                  json={"task": task, **kwargs})

    async def apply_blueprint(self, project_id: str, blueprint: dict[str, Any], expected_revision: int,
                              validate_only: bool = False):
        return await self.request("PUT", f"/api/agent/projects/{quote(project_id, safe='')}/blueprint",
                                  json={"blueprint": blueprint, "expected_revision": expected_revision,
                                        "validate_only": validate_only})

    async def set_prompt(self, project_id: str, segment_id: str, prompt: str, expected_revision: int,
                         validate_only: bool = False):
        return await self.request("PUT", f"/api/agent/projects/{quote(project_id, safe='')}/segments/{quote(segment_id, safe='')}/prompt",
                                  json={"prompt": prompt, "expected_revision": expected_revision,
                                        "validate_only": validate_only})

    async def submit(self, project_id: str, expected_revision: int, request_id: str,
                     kind: str = "video", **kwargs):
        return await self.request("POST", f"/api/agent/projects/{quote(project_id, safe='')}/jobs",
                                  json={"expected_revision": expected_revision, "request_id": request_id,
                                        "kind": kind, **kwargs})

    async def import_asset(self, project_id: str, path: str):
        source = Path(path)
        with source.open("rb") as file:
            return await self.request("POST", f"/api/projects/{quote(project_id, safe='')}/assets",
                                      files={"image": (source.name, file)})
