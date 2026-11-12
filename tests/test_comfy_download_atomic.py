import asyncio

import httpx
import pytest

from app.comfy_client import ComfyClient, ComfyOutput, ComfyUIError


@pytest.mark.parametrize("fail", [False, True])
def test_download_only_replaces_video_after_complete_transfer(monkeypatch, tmp_path, fail):
    from app import comfy_client

    destination = tmp_path / "output.mp4"
    destination.write_bytes(b"previous complete video")

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b"first"
            assert destination.read_bytes() == b"previous complete video"
            if fail:
                raise httpx.ReadError("connection dropped")
            yield b"second"

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

        def stream(self, *_args, **_kwargs):
            return Stream()

    monkeypatch.setattr(comfy_client.httpx, "AsyncClient", Client)
    output = ComfyOutput("remote.mp4", "", "output", "videos")
    download = ComfyClient("http://comfy").download(output, destination)
    if fail:
        with pytest.raises(ComfyUIError):
            asyncio.run(download)
        assert destination.read_bytes() == b"previous complete video"
    else:
        assert asyncio.run(download) == destination
        assert destination.read_bytes() == b"firstsecond"
    assert list(tmp_path.glob("*.part")) == []
