import asyncio
import json

import httpx
import pytest

from app import llama_service
from app.config import AppConfig
from app.llama_service import LlamaServerError, LlamaServerManager
from app.planner import StoryPlanner
from app.storage import ProjectStore


@pytest.mark.parametrize("content", ["partial {", ""])
def test_openrouter_failure_records_stream_before_parsing(tmp_path, monkeypatch, content):
    project_store = ProjectStore(tmp_path / "projects")
    project = project_store.create("test", "test story")
    manager = LlamaServerManager(AppConfig(llm_provider="openrouter"))
    event = {"model": "actual-model", "choices": [{"finish_reason": "length", "delta": {
        "content": content, "reasoning": "provider thinking"}}]}
    response = httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n")
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr(llama_service.httpx, "AsyncClient", lambda **kw: client_type(transport=transport, **kw))
    with pytest.raises(LlamaServerError):
        asyncio.run(StoryPlanner(project_store, manager).generate_blueprint(project["id"]))
    log, = (project_store.project_dir(project["id"]) / "logs").glob("blueprint_response_*.json")
    record = json.loads(log.read_text(encoding="utf-8"))
    assert record["content"] == content
    assert record["model"] == "actual-model"
    assert record["finish_reason"] == "length"
    assert record["reasoning"] == "provider thinking"
    assert record["error"]


def test_complete_markdown_json_fence(monkeypatch):
    manager = LlamaServerManager(AppConfig(llm_provider="openrouter"))

    async def completion(*args, **kwargs):
        return '```json\n{"segments": []}\n```'

    monkeypatch.setattr(manager, "completion", completion)
    assert asyncio.run(manager.json_completion([], {})) == {"segments": []}
