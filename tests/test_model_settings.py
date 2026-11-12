import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppConfig, llm_api_key, save_llm_api_key
from app.llama_service import LlamaServerManager
from app.main import app
from app.model_catalog import ModelCatalog


def test_home_exposes_model_settings_and_catalog() -> None:
    with TestClient(app) as client:
        home = client.get("/")
        settings = client.get("/api/config")

    assert 'id="model-settings-button"' in home.text
    assert settings.status_code == 200
    assert settings.json()["llm"]["provider"] in {"local", "ollama", "openai", "openrouter", "custom"}
    assert settings.json()["llm"]["active"]["model"]
    assert settings.json()["llm"]["active"]["provider_label"]
    assert all("api_key" not in profile for profile in settings.json()["llm"]["profiles"].values())


def test_model_catalog_separates_text_models_and_mmproj(tmp_path: Path) -> None:
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "story.Q4.gguf").write_bytes(b"model")
    (tmp_path / "models" / "mmproj-story.gguf").write_bytes(b"projector")
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "ignored.gguf").write_bytes(b"ignored")

    catalog = ModelCatalog(tmp_path)
    catalog.scan()

    assert [item["path"] for item in catalog.local_models] == ["models/story.Q4.gguf"]
    assert [item["path"] for item in catalog.mmproj_models] == ["models/mmproj-story.gguf"]


def test_api_key_round_trip_uses_dotenv_without_returning_plaintext(tmp_path: Path, monkeypatch) -> None:
    import app.config as config_module

    env_file = tmp_path / ".env"
    monkeypatch.setattr(config_module, "ENV_FILE", env_file)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    save_llm_api_key("openai", 'sk-value with spaces and "quotes"')
    assert llm_api_key("openai") == 'sk-value with spaces and "quotes"'
    assert "OPENAI_API_KEY=" in env_file.read_text(encoding="utf-8")

    save_llm_api_key("openai", None)
    assert llm_api_key("openai") == ""


def test_openai_compatible_completion_uses_selected_profile_and_key(monkeypatch) -> None:
    import app.llama_service as service_module

    config = AppConfig(
        llm_provider="openrouter",
        llm_profiles={
            "local": {"base_url": "", "model": "local.gguf"},
            "ollama": {"base_url": "http://127.0.0.1:11434", "model": ""},
            "openai": {"base_url": "https://api.openai.com/v1", "model": ""},
            "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "vendor/model"},
            "custom": {"base_url": "", "model": ""},
        },
    )
    manager = LlamaServerManager(config)
    captured = {}

    class Response:
        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "ok"}}]}

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, json, headers):
            captured.update(url=url, json=json, headers=headers)
            return Response()

    monkeypatch.setattr(service_module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(service_module, "llm_api_key", lambda _provider: "secret")

    assert asyncio.run(manager.completion([{"role": "user", "content": "hello"}])) == "ok"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["json"]["model"] == "vendor/model"
    assert captured["headers"]["Authorization"] == "Bearer secret"
