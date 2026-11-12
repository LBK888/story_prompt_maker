import asyncio
import json

import httpx

from app.comfy_client import ComfyClient


def test_free_memory_posts_both_comfy_release_flags(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr("app.comfy_client.httpx.AsyncClient",
                        lambda **kwargs: original_client(transport=transport, **kwargs))

    asyncio.run(ComfyClient("http://127.0.0.1:8188").free_memory())

    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:8188/free"
    assert json.loads(requests[0].content) == {"unload_models": True, "free_memory": True}
