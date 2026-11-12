from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "app.json"
ENV_FILE = ROOT / ".env"

LLM_PROVIDERS = ("local", "ollama", "openai", "openrouter", "custom")
LLM_API_KEY_ENV = {
    "ollama": "OLLAMA_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "custom": "LLM_API_KEY",
}


def default_llm_profiles() -> dict[str, dict[str, str]]:
    return {
        "local": {"base_url": "", "model": "Muse-Glimmer-30B-CRACK-Q4_K_M.gguf"},
        "ollama": {"base_url": "http://127.0.0.1:11434", "model": ""},
        "openai": {"base_url": "https://api.openai.com/v1", "model": ""},
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": ""},
        "custom": {"base_url": "", "model": ""},
    }


@dataclass(slots=True)
class AppConfig:
    execution_mode: str = "interactive"
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_shared_gpu: bool = True
    host: str = "127.0.0.1"
    port: int = 7865
    llama_host: str = "127.0.0.1"
    llama_port: int = 8081
    llama_context: int = 65536
    llama_executable: str = "runtime/llama.cpp/llama-server.exe"
    ffmpeg_executable: str = "runtime/ffmpeg/bin/ffmpeg.exe"
    ffprobe_executable: str = "runtime/ffmpeg/bin/ffprobe.exe"
    model_path: str = "Muse-Glimmer-30B-CRACK-Q4_K_M.gguf"
    mmproj_path: str = "mmproj-Muse-Glimmer-30B-f16.gguf"
    llm_api_timeout_seconds: int = 900
    llm_provider: str = "local"
    llm_profiles: dict[str, dict[str, str]] = field(default_factory=default_llm_profiles)

    @property
    def llama_url(self) -> str:
        return f"http://{self.llama_host}:{self.llama_port}"

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else ROOT / path

    @property
    def llama_executable_path(self) -> Path:
        return self.resolve_path(self.llama_executable)

    @property
    def ffmpeg_path(self) -> Path:
        return self.resolve_path(self.ffmpeg_executable)

    @property
    def ffprobe_path(self) -> Path:
        return self.resolve_path(self.ffprobe_executable)

    @property
    def model_file(self) -> Path:
        return self.resolve_path(self.model_path)

    @property
    def mmproj_file(self) -> Path:
        return self.resolve_path(self.mmproj_path)

    @property
    def llm_profile(self) -> dict[str, str]:
        return self.llm_profiles[self.llm_provider]

    @property
    def llm_model(self) -> str:
        return self.llm_profile.get("model", "").strip()

    @property
    def llm_base_url(self) -> str:
        return self.llm_profile.get("base_url", "").strip().rstrip("/")


def _coerce_config(raw: dict[str, Any]) -> AppConfig:
    allowed = set(AppConfig.__dataclass_fields__)
    values = {key: value for key, value in raw.items() if key in allowed}
    profiles = default_llm_profiles()
    for provider, profile in dict(values.get("llm_profiles") or {}).items():
        if provider in profiles and isinstance(profile, dict):
            profiles[provider].update({
                key: str(value) for key, value in profile.items() if key in {"base_url", "model"}
            })
    profiles["local"]["model"] = str(values.get("model_path") or profiles["local"]["model"])
    values["llm_profiles"] = profiles
    if values.get("llm_provider") not in LLM_PROVIDERS:
        values["llm_provider"] = "local"
    return AppConfig(**values)


def load_config() -> AppConfig:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        config = AppConfig()
        save_config(config)
    else:
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read {CONFIG_FILE}: {exc}") from exc
        config = _coerce_config(raw)
    config.execution_mode = os.environ.get("VID_AUTO_PROMPT_MODE", config.execution_mode)
    if config.execution_mode not in {"interactive", "agent"}:
        raise ValueError("VID_AUTO_PROMPT_MODE must be interactive or agent")
    env_url = os.environ.get("VID_AUTO_PROMPT_COMFY_URL", "").strip()
    if env_url:
        config.comfy_url = env_url
    return config


def save_config(config: AppConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".json.tmp")
    values = asdict(config)
    # --mode / VID_AUTO_PROMPT_MODE belongs to this backend process. Saving
    # ComfyUI settings in agent mode must not change the next interactive launch.
    values.pop("execution_mode", None)
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, CONFIG_FILE)


def _dotenv_values() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key] = value
    return values


def llm_api_key(provider: str) -> str:
    env_name = LLM_API_KEY_ENV.get(provider)
    if not env_name:
        return ""
    return os.environ.get(env_name, _dotenv_values().get(env_name, "")).strip()


def save_llm_api_key(provider: str, value: str | None) -> None:
    env_name = LLM_API_KEY_ENV.get(provider)
    if not env_name:
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(env_name)}\s*=")
    replacement = f"{env_name}={json.dumps(value, ensure_ascii=False)}" if value else None
    updated: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if replacement is not None and not replaced:
                updated.append(replacement)
                replaced = True
            continue
        updated.append(line)
    if replacement is not None and not replaced:
        updated.append(replacement)
    temporary = ENV_FILE.with_suffix(".env.tmp")
    temporary.write_text("\n".join(updated).rstrip() + ("\n" if updated else ""), encoding="utf-8")
    os.replace(temporary, ENV_FILE)
    if value:
        os.environ[env_name] = value
    else:
        os.environ.pop(env_name, None)
