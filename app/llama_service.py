from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from collections import deque
from pathlib import Path
from typing import Any

import httpx

from .config import AppConfig, ROOT, llm_api_key


class LlamaServerError(RuntimeError):
    pass


class LlamaServerManager:
    def _require_inference(self) -> None:
        if self.config.execution_mode == "agent":
            raise LlamaServerError("Built-in LLM is disabled in agent mode; get authoring context and submit the caller's result")

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        self.log_path = ROOT / "logs" / "llama-server.log"
        self.pid_path = ROOT / "logs" / "llama-server.pid.json"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle: Any = None
        self._inference: dict[str, Any] = {
            "busy": False,
            "operation": "",
            "stage": "",
            "progress": 0,
            "started_at": None,
            "current_tokens_per_second": 0.0,
            "average_tokens_per_second": 0.0,
            "generated_tokens": 0,
            "generation_seconds": 0.0,
            "speed_is_estimated": False,
        }

    def _write_pid_record(self) -> None:
        if not self.process:
            return
        self.pid_path.write_text(
            json.dumps(
                {
                    "pid": self.process.pid,
                    "executable": str(self.config.llama_executable_path.resolve()),
                    "port": self.config.llama_port,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _remove_pid_record(self) -> None:
        self.pid_path.unlink(missing_ok=True)

    @staticmethod
    def _process_executable(pid: int) -> Path | None:
        if os.name != "nt":
            try:
                return Path(f"/proc/{pid}/exe").resolve(strict=True)
            except (OSError, RuntimeError):
                return None
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.QueryFullProcessImageNameW.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            )
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return Path(buffer.value)
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, ValueError):
            return None

    def _is_expected_llama_process(self, pid: int) -> bool:
        executable = self._process_executable(pid)
        if not executable:
            return False
        expected = os.path.normcase(os.path.abspath(self.config.llama_executable_path))
        actual = os.path.normcase(os.path.abspath(executable))
        return actual == expected

    def _managed_listener_pid(self) -> int | None:
        if self.process and self.process.poll() is None:
            return self.process.pid
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=creationflags,
            )
            for line in result.stdout.splitlines():
                columns = line.split()
                if len(columns) < 5 or columns[0].upper() != "TCP" or not columns[-1].isdigit():
                    continue
                try:
                    port = int(columns[1].rsplit(":", 1)[1])
                except (IndexError, ValueError):
                    continue
                pid = int(columns[-1])
                if port == self.config.llama_port and columns[-2].upper().startswith("LISTEN") and self._is_expected_llama_process(pid):
                    return pid
        try:
            record = json.loads(self.pid_path.read_text(encoding="utf-8"))
            pid = int(record["pid"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return pid if self._is_expected_llama_process(pid) else None

    def running_pid(self) -> int | None:
        if self.config.execution_mode == "agent":
            return None
        return self._managed_listener_pid()

    @asynccontextmanager
    async def operation(self, label: str):
        self._require_inference()
        if self._inference_lock.locked():
            raise LlamaServerError("Muse-Glimmer is busy with another operation")
        await self._inference_lock.acquire()
        self._inference = {
            "busy": True,
            "operation": label,
            "stage": "準備模型",
            "progress": 1,
            "started_at": time.monotonic(),
            "current_tokens_per_second": 0.0,
            "average_tokens_per_second": 0.0,
            "generated_tokens": 0,
            "generation_seconds": 0.0,
            "speed_is_estimated": False,
        }
        try:
            yield
            self.update_progress(100, "完成")
        finally:
            self._inference["finished_at"] = time.monotonic()
            self._inference["busy"] = False
            self._inference_lock.release()

    def update_progress(self, progress: int | float, stage: str) -> None:
        self._inference["progress"] = max(0, min(100, round(float(progress))))
        self._inference["stage"] = stage

    def inference_status(self) -> dict[str, Any]:
        started_at = self._inference.get("started_at")
        elapsed = round((self._inference.get("finished_at") or time.monotonic()) - started_at, 1) if started_at else 0
        status = {**self._inference, "elapsed_seconds": elapsed}
        if status["busy"] and time.monotonic() - status.get("last_token_at", 0) > 3:
            status["current_tokens_per_second"] = 0.0
        return status

    async def health(self) -> bool:
        if self.config.execution_mode == "agent":
            return False
        if self.config.llm_provider == "local":
            return await self._local_health()
        headers = self._request_headers()
        url = (
            f"{self.config.llm_base_url}/api/tags"
            if self.config.llm_provider == "ollama"
            else f"{self.config.llm_base_url}/models"
        )
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(url, headers=headers)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def _local_health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{self.config.llama_url}/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def _request_headers(self) -> dict[str, str]:
        key = llm_api_key(self.config.llm_provider)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if self.config.llm_provider == "openrouter":
            headers.update({"HTTP-Referer": "http://127.0.0.1", "X-Title": "Vid Auto Prompt"})
        return headers

    def command(self) -> list[str]:
        command = [
            str(self.config.llama_executable_path),
            "--model", str(self.config.model_file),
            "--host", self.config.llama_host,
            "--port", str(self.config.llama_port),
            "--ctx-size", str(self.config.llama_context),
            "--n-gpu-layers", "999",
            "--parallel", "1",
            "--flash-attn", "on",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--jinja",
        ]
        if self.config.mmproj_path.strip():
            command[3:3] = ["--mmproj", str(self.config.mmproj_file)]
        return command

    def preflight(self) -> list[str]:
        if self.config.execution_mode == "agent":
            return []
        if self.config.llm_provider != "local":
            missing = []
            if not self.config.llm_base_url:
                missing.append("尚未設定 API URL")
            if not self.config.llm_model:
                missing.append("尚未選擇 API 模型")
            return missing
        missing: list[str] = []
        files = [
            ("llama-server", self.config.llama_executable_path),
            ("GGUF model", self.config.model_file),
        ]
        if self.config.mmproj_path.strip():
            files.append(("GGUF mmproj", self.config.mmproj_file))
        for label, path in files:
            if not path.is_file():
                missing.append(f"{label}: {path}")
        return missing

    async def ensure_started(self, timeout_seconds: int = 300) -> None:
        self._require_inference()
        if self.config.llm_provider != "local":
            return
        async with self._lock:
            if await self._local_health():
                return
            missing = self.preflight()
            if missing:
                raise LlamaServerError("Missing llama.cpp runtime files:\n" + "\n".join(missing))
            if self.process and self.process.poll() is None:
                raise LlamaServerError("llama-server process exists but its health endpoint is unavailable")
            self._log_handle = self.log_path.open("ab", buffering=0)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.process = subprocess.Popen(
                self.command(),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self._write_pid_record()
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                if self.process.poll() is not None:
                    self._remove_pid_record()
                    raise LlamaServerError(
                        f"llama-server exited with code {self.process.returncode}; see {self.log_path}"
                    )
                if await self._local_health():
                    return
                await asyncio.sleep(1)
            process = self.process
            if process and process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait)
            self.process = None
            self._remove_pid_record()
            raise LlamaServerError(f"llama-server did not become healthy; see {self.log_path}")

    async def stop(self) -> bool:
        if self.config.execution_mode == "agent":
            return False
        async with self._lock:
            process = self.process
            stopped = False
            if process and process.poll() is None:
                process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=20)
                except TimeoutError:
                    process.kill()
                    await asyncio.to_thread(process.wait)
                stopped = True
            else:
                pid = self._managed_listener_pid()
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    else:
                        stopped = True
                        deadline = asyncio.get_running_loop().time() + 20
                        while await self._local_health() and asyncio.get_running_loop().time() < deadline:
                            await asyncio.sleep(0.2)
                        if await self._local_health():
                            raise LlamaServerError("llama-server did not stop within 20 seconds")
            self.process = None
            self._remove_pid_record()
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            return stopped

    async def completion(
        self,
        messages: list[dict[str, Any]],
        mode: str = "balanced",
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        self._require_inference()
        provider = self.config.llm_provider
        await self.ensure_started()
        temperatures = {"strict": 0.2, "balanced": 0.7, "creative": 1.0}
        payload: dict[str, Any] = {
            "model": self.config.llm_model or self.config.model_file.stem,
            "messages": messages,
            "temperature": temperatures.get(mode, 0.7),
            "top_p": 0.95,
            "stream": bool(self._inference["busy"]),
        }
        if json_schema is not None and provider == "ollama":
            payload["format"] = json_schema
        elif json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "video_blueprint", "strict": True, "schema": json_schema},
            }
        if payload["stream"] and provider == "local":
            payload["stream_options"] = {"include_usage": True}
        timeout = httpx.Timeout(self.config.llm_api_timeout_seconds, connect=10)
        if provider == "local":
            url = f"{self.config.llama_url}/v1/chat/completions"
        elif provider == "ollama":
            url = f"{self.config.llm_base_url}/api/chat"
            payload["options"] = {
                "temperature": payload.pop("temperature"),
                "top_p": payload.pop("top_p"),
            }
        else:
            url = f"{self.config.llm_base_url}/chat/completions"
        if payload["stream"]:
            return await self._stream_completion(url, payload, provider, timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=self._request_headers())
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LlamaServerError(f"LLM request failed ({provider}): {exc}") from exc
        try:
            content = (
                data["message"]["content"]
                if provider == "ollama"
                else data["choices"][0]["message"]["content"]
            )
        except (KeyError, IndexError, TypeError) as exc:
            raise LlamaServerError("LLM response is missing message content") from exc
        if isinstance(content, list):
            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        if not isinstance(content, str) or not content.strip():
            raise LlamaServerError("LLM returned an empty response")
        return content.strip()

    async def _stream_completion(
        self, url: str, payload: dict[str, Any], provider: str, timeout: httpx.Timeout
    ) -> str:
        pieces: list[str] = []
        token_times: deque[float] = deque()
        first_token_at: float | None = None
        observed_tokens = 0
        reported_tokens: int | None = None
        reported_duration: float | None = None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=self._request_headers()) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if provider == "ollama":
                            raw = line
                        elif line.startswith("data:"):
                            raw = line[5:].strip()
                            if raw == "[DONE]":
                                break
                        else:
                            continue
                        event = json.loads(raw)
                        if event.get("error"):
                            raise LlamaServerError(str(event["error"]))
                        usage = event.get("usage") or {}
                        if usage.get("completion_tokens") is not None:
                            reported_tokens = int(usage["completion_tokens"])
                        if provider == "ollama":
                            delta = event.get("message") or {}
                            content = delta.get("content") or ""
                            reasoning = delta.get("thinking") or ""
                            if event.get("done") and event.get("eval_count") is not None:
                                reported_tokens = int(event["eval_count"])
                                duration = float(event.get("eval_duration") or 0) / 1_000_000_000
                                reported_duration = duration if duration > 0 else None
                        else:
                            choices = event.get("choices") or []
                            delta = choices[0].get("delta") or {} if choices else {}
                            content = delta.get("content") or ""
                            reasoning = delta.get("reasoning_content") or ""
                        if isinstance(content, list):
                            content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
                        if content:
                            pieces.append(str(content))
                        if content or reasoning:
                            now = time.monotonic()
                            self._inference["last_token_at"] = now
                            first_token_at = first_token_at or now
                            observed_tokens += 1
                            token_times.append(now)
                            while token_times and now - token_times[0] > 3:
                                token_times.popleft()
                            window = min(3.0, max(now - first_token_at, 0.2))
                            self._inference["current_tokens_per_second"] = round(len(token_times) / window, 1)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise LlamaServerError(f"LLM request failed ({provider}): {exc}") from exc
        duration = reported_duration or (time.monotonic() - first_token_at if first_token_at else 0)
        count = reported_tokens if reported_tokens is not None else observed_tokens
        if reported_tokens is None:
            self._inference["speed_is_estimated"] = True
        if duration > 0 and count:
            self._inference["generated_tokens"] += count
            self._inference["generation_seconds"] += duration
            self._inference["average_tokens_per_second"] = round(
                self._inference["generated_tokens"] / self._inference["generation_seconds"], 1
            )
        content = "".join(pieces).strip()
        if not content:
            raise LlamaServerError("LLM returned an empty response")
        return content

    async def json_completion(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        mode: str = "balanced",
    ) -> dict[str, Any]:
        content = await self.completion(messages, mode=mode, json_schema=schema)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlamaServerError(f"LLM returned invalid JSON: {exc}") from exc
