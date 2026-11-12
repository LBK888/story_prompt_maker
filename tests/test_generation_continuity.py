import asyncio
from pathlib import Path

from app.config import AppConfig
from app.generation import GenerationManager


class StoreStub:
    def __init__(self, root: Path) -> None:
        self.root = root

    def project_dir(self, project_id: str) -> Path:
        return self.root


class EventsStub:
    async def publish(self, project_id: str, event: dict) -> None:
        return None


def test_continuity_frame_tracks_the_current_active_output(monkeypatch, tmp_path: Path) -> None:
    old_path = "segments/segment_001/take_1/old.mp4"
    new_path = "segments/segment_001/take_1/new.mp4"
    for relative in (old_path, new_path):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")

    previous = {
        "id": "segment_001",
        "index": 1,
        "status": "failed",
        "active_take_id": "take_1",
        "takes": [
            {
                "id": "take_1",
                "status": "completed",
                "outputs": [old_path, new_path],
                "local_path": old_path,
            }
        ],
    }
    current = {"id": "segment_002", "index": 2, "continuity_from_previous": True}
    project = {"segments": [previous, current]}
    extracted: list[tuple[Path, Path]] = []

    async def fake_extract(ffmpeg_path: str, source: Path, destination: Path) -> None:
        extracted.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"frame")

    monkeypatch.setattr("app.generation.extract_last_frame", fake_extract)
    manager = GenerationManager(StoreStub(tmp_path), None, None, AppConfig(), EventsStub())  # type: ignore[arg-type]

    async def scenario() -> None:
        first = await manager._continuity_frame("project_1", project, current)
        previous["takes"][0]["local_path"] = new_path
        second = await manager._continuity_frame("project_1", project, current)
        assert first != second

    asyncio.run(scenario())
    assert [source for source, _ in extracted] == [tmp_path / old_path, tmp_path / new_path]


def test_removed_active_output_is_not_a_valid_continuity_source(tmp_path: Path) -> None:
    manager = GenerationManager(StoreStub(tmp_path), None, None, AppConfig(), EventsStub())  # type: ignore[arg-type]
    segment = {
        "active_take_id": "take_1",
        "takes": [
            {
                "id": "take_1",
                "status": "completed",
                "outputs": ["still-attached.mp4"],
                "local_path": "removed.mp4",
            }
        ],
    }

    assert manager._active_take(segment) is None


def test_first_frame_uses_continuity_only_without_selected_asset(monkeypatch, tmp_path: Path) -> None:
    manager = GenerationManager(StoreStub(tmp_path), None, None, AppConfig(), EventsStub())  # type: ignore[arg-type]
    frame = tmp_path / "previous-last.jpg"
    frame.write_bytes(b"frame")
    calls: list[Path] = []

    class Client:
        async def upload_input(self, path: Path, remote_name: str):
            calls.append(path)
            return {"name": remote_name, "subfolder": ""}

    async def continuity(*_args):
        return frame

    monkeypatch.setattr(manager, "_continuity_frame", continuity)
    segment = {"task_type": "I2VA", "template": {"first_frame_asset_id": None, "image_order": []}}
    project = {"assets": []}
    asyncio.run(manager._upload_inputs(Client(), "project", project, segment, ("FIRST_FRAME",)))
    assert calls == [frame]

    asset_path = tmp_path / "ref_img" / "start.jpg"
    asset_path.parent.mkdir()
    asset_path.write_bytes(b"selected")
    project["assets"] = [{"id": "first", "filename": "start.jpg", "sha256": "123456789abcdef",
                          "media_type": "image"}]
    segment["template"]["first_frame_asset_id"] = "first"
    calls.clear()
    asyncio.run(manager._upload_inputs(Client(), "project", project, segment, ("FIRST_FRAME",)))
    assert calls == [asset_path]
