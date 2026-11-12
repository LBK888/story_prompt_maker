from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx

from app.config import llm_api_key, load_config


BENCHMARK_PROMPT = (
    "Write a concise technical explanation of why stable character and scene descriptions "
    "matter across independently generated video clips. Use about 180 English words, no lists."
)


def benchmark_once(max_tokens: int, model_override: str = "") -> dict[str, Any]:
    config = load_config()
    provider = config.llm_provider
    if provider not in {"local", "openai", "openrouter", "custom"}:
        raise RuntimeError("This streaming benchmark supports local and OpenAI-compatible providers")
    url = (
        f"{config.llama_url}/v1/chat/completions"
        if provider == "local"
        else f"{config.llm_base_url}/chat/completions"
    )
    key = llm_api_key(provider)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": "http://127.0.0.1", "X-Title": "Vid Auto Prompt benchmark"})
    model = model_override or config.llm_model or config.model_file.stem
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": BENCHMARK_PROMPT}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": True,
    }
    preflight_seconds: float | None = None
    preflight_status: int | None = None
    started = time.perf_counter()
    first_token_at: float | None = None
    output_chars = 0
    reasoning_chars = 0
    content_events = 0
    reasoning_events = 0
    usage: dict[str, Any] = {}
    finish_reason = None
    stream_error = None
    generation_id = ""
    generation_metadata: dict[str, Any] = {}
    with httpx.Client(timeout=httpx.Timeout(180, connect=15)) as client:
        preflight_started = time.perf_counter()
        models_url = f"{config.llama_url}/v1/models" if provider == "local" else f"{config.llm_base_url}/models"
        with client.stream("GET", models_url, headers=headers) as preflight_response:
            preflight_status = preflight_response.status_code
        preflight_seconds = time.perf_counter() - preflight_started
        started = time.perf_counter()
        with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.is_error:
                error_body = response.read().decode("utf-8", errors="replace")[:2000]
                raise RuntimeError(
                    f"Provider returned HTTP {response.status_code} "
                    f"(control plane {preflight_status} in {preflight_seconds:.3f}s): {error_body}"
                )
            connected_at = time.perf_counter()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                event = json.loads(raw)
                generation_id = str(event.get("id") or generation_id)
                if event.get("error"):
                    stream_error = event["error"]
                if event.get("usage"):
                    usage = event["usage"]
                try:
                    choice = event["choices"][0]
                    delta = choice["delta"]
                    content = delta.get("content") or ""
                    reasoning = delta.get("reasoning") or ""
                    finish_reason = choice.get("finish_reason") or finish_reason
                except (KeyError, IndexError, TypeError):
                    content = ""
                    reasoning = ""
                if content or reasoning:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                if content:
                    output_chars += len(content)
                    content_events += 1
                if reasoning:
                    reasoning_chars += len(reasoning)
                    reasoning_events += 1
        if provider == "openrouter" and generation_id:
            for _ in range(3):
                metadata_response = client.get(
                    f"{config.llm_base_url}/generation",
                    params={"id": generation_id},
                    headers=headers,
                )
                if metadata_response.status_code == 200:
                    generation_metadata = metadata_response.json().get("data") or {}
                    break
                time.sleep(0.5)
    finished = time.perf_counter()
    completion_tokens = (
        generation_metadata.get("native_tokens_completion")
        or usage.get("completion_tokens")
        or usage.get("output_tokens")
    )
    estimated_tokens = max(1, round((output_chars + reasoning_chars) / 4))
    measured_tokens = int(completion_tokens or estimated_tokens)
    generation_seconds = max(0.001, finished - (first_token_at or connected_at))
    return {
        "provider": provider,
        "model": model,
        "control_plane_status": preflight_status,
        "control_plane_seconds": round(preflight_seconds, 3) if preflight_seconds is not None else None,
        "http_connect_seconds": round(connected_at - started, 3),
        "time_to_first_token_seconds": round((first_token_at or finished) - started, 3),
        "total_seconds": round(finished - started, 3),
        "completion_tokens": completion_tokens,
        "estimated_tokens_if_usage_missing": estimated_tokens,
        "content_events": content_events,
        "output_characters": output_chars,
        "reasoning_events": reasoning_events,
        "reasoning_characters": reasoning_chars,
        "finish_reason": finish_reason,
        "stream_error": stream_error,
        "upstream_provider": generation_metadata.get("provider_name"),
        "upstream_generation_time_ms": generation_metadata.get("generation_time"),
        "openrouter_latency_ms": generation_metadata.get("latency"),
        "native_prompt_tokens": generation_metadata.get("native_tokens_prompt"),
        "native_reasoning_tokens": generation_metadata.get("native_tokens_reasoning"),
        "tokens_per_second_after_first": round(measured_tokens / generation_seconds, 2),
        "tokens_per_second_end_to_end": round(measured_tokens / max(0.001, finished - started), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure active LLM streaming latency without printing secrets or output text")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--model", default="", help="Benchmark another model without changing saved settings")
    args = parser.parse_args()
    results = []
    for _ in range(max(1, args.runs)):
        try:
            results.append(benchmark_once(args.max_tokens, args.model))
        except Exception as exc:  # Keep provider failures readable for diagnostics.
            results.append({"model": args.model or load_config().llm_model, "error": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
