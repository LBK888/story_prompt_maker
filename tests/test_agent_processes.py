"""Real process/transport smoke tests; do not contact an LLM or ComfyUI."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from test_agent_authoring import blueprint


ROOT = Path(__file__).resolve().parent.parent
HIDDEN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.fixture
def running_backend(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    environment = {**os.environ, "VID_AUTO_PROMPT_MODE": "agent",
                   "VID_AUTO_PROMPT_PROJECTS_DIR": str(tmp_path / "projects")}
    with (tmp_path / "backend.log").open("w+", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--mode", "agent", "--port", str(port)],
                                   cwd=ROOT, env=environment, stdout=log, stderr=log, creationflags=HIDDEN)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read())
                try:
                    with urllib.request.urlopen(url + "/api/agent/capabilities", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise AssertionError("Backend did not start")
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_cli_process_from_another_directory(running_backend, tmp_path):
    def cli(*args):
        return subprocess.run([sys.executable, str(ROOT / "vap.py"), "--url", running_backend, *args],
                              cwd=tmp_path, text=True, encoding="utf-8", capture_output=True,
                              timeout=15, creationflags=HIDDEN)
    capabilities = cli("capabilities")
    assert capabilities.returncode == 0, capabilities.stderr
    assert json.loads(capabilities.stdout)["built_in_llm_enabled"] is False
    created = cli("project", "create", "--name", "跨程序測試", "--story", "A painter opens a window.")
    assert created.returncode == 0, created.stderr
    project = json.loads(created.stdout)
    context = cli("context", "--project", project["id"], "--task", "blueprint")
    assert context.returncode == 0, context.stderr
    assert json.loads(context.stdout)["inference"] == "caller"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    rejected = cli("blueprint", "apply", "--project", project["id"], "--expected-revision",
                   str(project["revision"]), "--file", str(invalid))
    assert rejected.returncode == 1
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr)["error"]["code"] == "invalid_blueprint"


def test_stdio_mcp_process_with_real_http_backend(running_backend):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
                                           args=[str(ROOT / "vap_mcp.py"), "--url", running_backend])
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                capabilities = await session.call_tool("get_capabilities")
                assert capabilities.structuredContent["execution_mode"] == "agent"
                created = await session.call_tool("create_project", {"name": "MCP process", "story": "A sunrise"})
                project = created.structuredContent
                settings = await session.call_tool("update_settings", {"project_id": project["id"],
                    "expected_revision": project["revision"], "changes": {"minimum_duration_seconds": 8}})
                applied = await session.call_tool("apply_blueprint", {"project_id": project["id"],
                    "expected_revision": settings.structuredContent["revision"], "blueprint": blueprint()})
                assert not applied.isError, applied
                assert len(applied.structuredContent["project"]["segments"]) == 2
                context = await session.call_tool("get_authoring_context", {"project_id": project["id"],
                    "task": "prompt", "segment_id": "segment_001"})
                assert context.structuredContent["inference"] == "caller"

    asyncio.run(scenario())
