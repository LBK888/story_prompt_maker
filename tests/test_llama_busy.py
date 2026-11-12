import asyncio

import pytest

from app.config import AppConfig
from app.llama_service import LlamaServerError, LlamaServerManager


def test_llama_operation_reports_progress_and_rejects_parallel_work() -> None:
    async def scenario() -> None:
        manager = LlamaServerManager(AppConfig())
        async with manager.operation("規劃影片藍圖"):
            manager.update_progress(42, "正在生成 JSON")
            status = manager.inference_status()
            assert status["busy"] is True
            assert status["progress"] == 42
            assert status["stage"] == "正在生成 JSON"
            with pytest.raises(LlamaServerError, match="busy"):
                async with manager.operation("另一個工作"):
                    pass
        assert manager.inference_status()["busy"] is False

    asyncio.run(scenario())
