import asyncio
import importlib

from app.config import AppConfig
from app.llama_service import LlamaServerManager


def test_unload_llama_interrupts_running_manager(monkeypatch) -> None:
    main = importlib.import_module("app.main")

    class RunningProcess:
        @staticmethod
        def poll() -> None:
            return None

    class LlamaStub:
        process = RunningProcess()
        stopped = False

        @staticmethod
        def inference_status() -> dict[str, bool]:
            return {"busy": True}

        @staticmethod
        def running_pid() -> int:
            return 23648

        async def stop(self) -> bool:
            self.stopped = True
            self.process = None
            return True

    stub = LlamaStub()
    monkeypatch.setattr(main, "llama", stub)

    result = asyncio.run(main.unload_llama())

    assert stub.stopped is True
    assert result == {
        "ok": True,
        "interrupted": True,
        "was_running": True,
        "message": "LLM 已中斷並釋放 VRAM",
    }


def test_stop_recovers_orphan_llama_server_from_its_listening_port(monkeypatch) -> None:
    manager = LlamaServerManager(AppConfig())
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(manager, "_managed_listener_pid", lambda: 23648)
    monkeypatch.setattr("app.llama_service.os.kill", lambda pid, sig: killed.append((pid, sig)))
    async def unhealthy() -> bool:
        return False

    monkeypatch.setattr(manager, "health", unhealthy)

    assert asyncio.run(manager.stop()) is True
    assert killed and killed[0][0] == 23648
