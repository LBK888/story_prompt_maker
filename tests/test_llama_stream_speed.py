import asyncio
import json

from app.config import AppConfig
from app.llama_service import LlamaServerManager


def test_streamed_completion_exposes_speed_and_final_usage(monkeypatch):
    from app import llama_service

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_lines(self):
            for data in (
                {"choices": [{"delta": {"content": "Hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
                {"choices": [], "usage": {"completion_tokens": 12}},
            ):
                yield "data: " + json.dumps(data)
            yield "data: [DONE]"

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            pass

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        def stream(self, method, url, json, headers):
            assert method == "POST"
            assert json["stream"] is True
            return Stream()

    monkeypatch.setattr(llama_service.httpx, "AsyncClient", Client)
    manager = LlamaServerManager(AppConfig(llm_provider="openai"))

    async def run():
        async with manager.operation("test"):
            content = await manager.completion([{"role": "user", "content": "Hi"}])
            live = manager.inference_status()
        return content, live, manager.inference_status()

    content, live, finished = asyncio.run(run())
    assert content == "Hello world"
    assert live["busy"] is True
    assert live["current_tokens_per_second"] > 0
    assert finished["busy"] is False
    assert finished["generated_tokens"] == 12
    assert finished["average_tokens_per_second"] > 0
