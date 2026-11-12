"""Optional Qwen Image storyboard stills."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .comfy_client import ComfyClient
from .config import AppConfig
from .domain import new_id, utc_now
from .llama_service import LlamaServerManager
from .media import attach_images, ensure_analysis_copy
from .reference_order import bound_reference_asset_ids
from .storage import ProjectStore, TEMPLATES_DIR
from .template_engine import load_template, render_template
from .video import _run

KINDS = {"first_frame": "起始幀", "last_frame": "結束幀", "scene": "場景圖"}
logger = logging.getLogger(__name__)


def image_prompt_messages(project: dict[str, Any], segment: dict[str, Any], kind: str, edit: bool) -> list[dict[str, Any]]:
    system = (
        "Write one precise English image-edit instruction for Qwen-Image-2.1. "
        "Use Reference Image 1, 2, 3 by their order; retain identities and distinctive details. "
        "Describe the desired changes and final composition, without resolution or aspect-ratio numbers. "
        "Do not invent visible text. Return only the instruction."
        if edit else
        "Write one detailed English text-to-image prompt for Qwen-Image-2.1. "
        "Describe the finished image as a single coherent visual paragraph: subject, action, composition, "
        "camera, environment, style, lighting and colors. Include the requested aspect ratio. "
        "Do not invent visible text. Return only the prompt."
    )
    user = json.dumps({"image_kind": KINDS[kind], "segment_title": segment.get("title"),
                       "summary": segment["summary"], "shots": segment["shots"],
                       "aspect_ratio": None if edit else project["settings"]["aspect_ratio"],
                       "style_category": project["settings"].get("style_category", ""),
                       "style": project["settings"].get("style_notes", "")}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def image_dimensions(aspect_ratio: str) -> tuple[int, int]:
    from .domain import ASPECT_RATIOS
    import math
    if aspect_ratio not in ASPECT_RATIOS:
        raise ValueError("Unsupported aspect ratio")
    x, y = ASPECT_RATIOS[aspect_ratio]
    # Slightly above the project's largest 2 MP video option, while staying near 2 MP.
    height = round(math.sqrt(2_250_000 * y / x) / 8) * 8
    width = round((height * x / y) / 8) * 8
    return width, height


class SegmentImageService:
    def __init__(self, store: ProjectStore, llama: LlamaServerManager, config: AppConfig):
        self.store, self.llama, self.config = store, llama, config

    def context(self, project_id: str, segment_id: str, kind: str, asset_ids: list[str]):
        if kind not in KINDS or len(asset_ids) > 3 or len(set(asset_ids)) != len(asset_ids):
            raise ValueError("請選擇圖片種類及最多 3 個不同參考素材")
        project = self.store.load(project_id)
        segment = next((s for s in project.get("segments", []) if s.get("id") == segment_id), None)
        if segment is None:
            raise ValueError("找不到分鏡")
        if not str(segment.get("summary") or "").strip() or not segment.get("shots"):
            raise ValueError("請先填寫並儲存片段摘要與鏡頭 JSON")
        assets = {a["id"]: a for a in project.get("assets", [])}
        if any(aid not in assets or assets[aid].get("media_type") not in {"image", "character", "video"} for aid in asset_ids):
            raise ValueError("參考素材只能選圖片、角色或影片")
        return project, segment, [assets[aid] for aid in asset_ids]

    async def _reference_paths(self, project_id: str, assets: list[dict[str, Any]]) -> list[tuple[str, Path]]:
        paths = []
        for index, asset in enumerate(assets, start=1):
            if asset.get("media_type") == "video":
                source = self.store.project_dir(project_id) / "ref_img" / asset["filename"]
                path = self.store.project_dir(project_id) / "ref_img" / ".analysis" / f"{asset['sha256'][:16]}.jpg"
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    await _run([str(self.config.ffmpeg_path), "-y", "-i", str(source), "-frames:v", "1", str(path)])
            else:
                path = ensure_analysis_copy(self.store, project_id, asset)
            paths.append((f"Reference Image {index}: {asset.get('original_name', '')}", path))
        return paths

    async def prompt(self, project_id: str, segment_id: str, kind: str, asset_ids: list[str]) -> str:
        project, segment, assets = self.context(project_id, segment_id, kind, asset_ids)
        messages = image_prompt_messages(project, segment, kind, bool(assets))
        messages = attach_images(messages, await self._reference_paths(project_id, assets))
        async with self.llama.operation(f"產生 {segment_id} {KINDS[kind]}提示詞"):
            return await self.llama.completion(messages, mode="strict")

    async def _filename_title(self, segment: dict[str, Any], kind: str, prompt: str, assets: list[dict[str, Any]]) -> str:
        messages = [
            {"role": "system", "content": "為圖片取一個能辨識畫面內容的短檔名。只輸出 1 到 10 個中文字、英文字母或數字；不要副檔名、標點、引號、說明或前綴。"},
            {"role": "user", "content": json.dumps({
                "類型": KINDS[kind], "片段摘要": segment["summary"], "鏡頭": segment["shots"],
                "圖片提示詞": prompt, "參考素材": [asset.get("original_name", "") for asset in assets],
            }, ensure_ascii=False)},
        ]
        async with self.llama.operation(f"為 {segment['id']} 圖片命名"):
            response = await self.llama.completion(messages, mode="strict")
        response = re.sub(r"^(?:檔名|名稱|標題)\s*[:：]\s*", "", response.strip())
        title = "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", response))[:10]
        if not title:
            raise ValueError("LLM 未產生可用的圖片名稱，請重試")
        return title

    def candidates(self, project_id: str, segment_id: str, kind: str) -> list[dict[str, Any]]:
        if kind not in KINDS:
            raise ValueError("不支援的圖片類型")
        project = self.store.load(project_id)
        segment = next((s for s in project.get("segments", []) if s.get("id") == segment_id), None)
        if segment is None:
            raise ValueError("找不到分鏡")
        return [candidate for candidate in segment.get("image_candidates", []) if candidate.get("kind") == kind]

    async def generate(self, project_id: str, segment_id: str, kind: str, asset_ids: list[str], prompt: str) -> dict[str, Any]:
        """Keep the single-image service interface for existing callers."""
        result = await self.generate_batch(project_id, segment_id, kind, asset_ids, prompt, 1)
        return result["candidates"][0]

    async def generate_batch(self, project_id: str, segment_id: str, kind: str,
                             asset_ids: list[str], prompt: str, batch_count: int = 1,
                             *, output_title: str | None = None, use_llm: bool = True) -> dict[str, Any]:
        if type(batch_count) is not int or not 1 <= batch_count <= 4:
            raise ValueError("生成數量必須是 1–4 張")
        project, segment, assets = self.context(project_id, segment_id, kind, asset_ids)
        if not prompt.strip():
            raise ValueError("請先生成或填寫提示詞")
        if output_title is not None or not use_llm or self.config.execution_mode == "agent":
            title = "".join(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", output_title or segment.get("title", "")))[:10] or kind
        else:
            title = await self._filename_title(segment, kind, prompt.strip(), assets)
        name = f"qimg_2_1_edit_{len(assets)}ref2i_template.json" if assets else "qimg_2_1_t2i_template.json"
        template = load_template(TEMPLATES_DIR / name)
        client = ComfyClient(self.config.comfy_url)
        await client.health()
        width, height = image_dimensions(project["settings"]["aspect_ratio"])
        used_seeds = {candidate.get("seed") for candidate in segment.get("image_candidates", [])}
        reference_inputs: dict[str, str] = {}
        for index, (_, path) in enumerate(await self._reference_paths(project_id, assets)):
            uploaded = await client.upload_input(path, f"vid_auto_prompt/{project_id}/{new_id('image')}_ref{index}.jpg")
            reference_inputs[f"REF_IMG{index}"] = f"{uploaded.get('subfolder', '')}/{uploaded['name']}".strip("/")
        local_comfy_and_llm = (
            self.config.execution_mode != "agent" and
            getattr(getattr(self.llama, "config", None), "llm_provider", None) == "local" and
            (urlparse(self.config.comfy_url).hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        )
        if local_comfy_and_llm and self.config.comfy_shared_gpu:
            await self.llama.stop()
        result: dict[str, Any] = {"candidates": [], "errors": [], "memory_release_error": None}
        submitted: list[tuple[int, str, str, str, int]] = []
        try:
            # Each count is a separate workflow with its own seed and output prefix.
            # The template's per-workflow batch setting stays at the existing
            # single-image behavior; it is never used as the UI batch count.
            for number in range(1, batch_count + 1):
                run_id = new_id("image")
                prefix = f"vid_auto_prompt/{project_id}/{segment_id}/{run_id}"
                seed = secrets.randbelow(2**63 - 1)
                while seed in used_seeds:
                    seed = secrets.randbelow(2**63 - 1)
                used_seeds.add(seed)
                replacements: dict[str, Any] = {"PROMPT": prompt.strip(), "SEED": seed,
                                                "WIDTH": width, "HEIGHT": height,
                                                "OUTPUT_PREFIX": prefix, **reference_inputs}
                workflow = render_template(template, replacements)
                for node in workflow.values():
                    if node.get("class_type") == "EmptyLatentImage":
                        node["inputs"]["batch_size"] = 1
                try:
                    prompt_id = await client.submit(workflow)
                except Exception as exc:
                    result["errors"].append(f"第 {number} 張送出失敗：{exc}")
                    continue
                submitted.append((number, run_id, prefix, prompt_id, seed))

            for number, run_id, prefix, prompt_id, seed in submitted:
                try:
                    history = await client.wait(prompt_id)
                    outputs = [o for o in client.outputs(history) if o.media_type == "images" and
                               (o.relative_name.startswith(prefix) or o.filename.startswith(run_id))]
                    if not outputs:
                        raise ValueError(f"ComfyUI 未回傳符合 {prefix} 的圖片")
                    temporary = self.store.project_dir(project_id) / "generated_images" / f"{run_id}.png"
                    await client.download(outputs[0], temporary)
                    with self.store._lock:
                        project = self.store.load(project_id)
                        segment = next((s for s in project["segments"] if s["id"] == segment_id), None)
                        if segment is None:
                            raise ValueError("生成期間分鏡已刪除")
                        stem = f"seg{int(segment['index']):03d}_{title}_{'scene' if kind == 'scene' else 'frame'}"
                        filename = f"{stem}.png"
                        counter = 1
                        generated_dir = self.store.project_dir(project_id) / "generated_images"
                        ref_dir = self.store.project_dir(project_id) / "ref_img"
                        while (generated_dir / filename).exists() or (ref_dir / filename).exists():
                            filename = f"{stem}({counter}).png"
                            counter += 1
                        os.replace(temporary, generated_dir / filename)
                        candidate = {
                            "id": run_id, "segment_id": segment_id, "kind": kind,
                            "filename": filename, "path": f"generated_images/{filename}",
                            "prompt": prompt.strip(), "seed": seed, "prompt_id": prompt_id,
                            "reference_asset_ids": asset_ids, "created_at": utc_now(), "adopted_asset_id": None,
                        }
                        segment.setdefault("image_candidates", []).append(candidate)
                        self.store.save(project)
                    result["candidates"].append(candidate)
                except Exception as exc:
                    result["errors"].append(f"第 {number} 張取回失敗：{exc}")
        finally:
            if local_comfy_and_llm:
                try:
                    await client.free_memory()
                except Exception as exc:
                    result["memory_release_error"] = str(exc)
                    logger.warning("ComfyUI memory release failed after image generation: %s", exc)
        if not result["candidates"] and result["errors"]:
            raise ValueError("；".join(result["errors"]))
        return result

    def adopt(self, project_id: str, segment_id: str, candidate_id: str) -> dict[str, Any]:
        with self.store._lock:
            project = self.store.load(project_id)
            segment = next((s for s in project.get("segments", []) if s.get("id") == segment_id), None)
            if segment is None:
                raise ValueError("找不到分鏡")
            candidate = next((c for c in segment.get("image_candidates", []) if c.get("id") == candidate_id), None)
            if candidate is None:
                raise ValueError("找不到生成圖片")
            filename = candidate["filename"]
            source = (self.store.project_dir(project_id) / "generated_images" / filename).resolve()
            if source.parent != (self.store.project_dir(project_id) / "generated_images").resolve() or not source.is_file():
                raise ValueError("生成圖片檔案不存在")
            asset = self.store.import_asset(project_id, source, filename, preserve_name=True)
            project = self.store.load(project_id)
            segment = next(s for s in project["segments"] if s["id"] == segment_id)
            candidate = next(c for c in segment["image_candidates"] if c["id"] == candidate_id)
            kind = candidate["kind"]
            old = segment.setdefault("generated_image_assets", {}).get(kind)
            segment["generated_image_assets"][kind] = asset["id"]
            segment.setdefault("selected_image_candidate_ids", {})[kind] = candidate_id
            if kind in {"first_frame", "last_frame"} and segment.get("template"):
                segment["template"][f"{kind}_asset_id"] = asset["id"]
            if kind == "scene":
                segment["scene_ref_asset_id"] = asset["id"]
            saved_asset = next(a for a in project["assets"] if a["id"] == asset["id"])
            roles = saved_asset.setdefault("roles", [])
            if kind not in roles:
                roles.append(kind)
            segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
            if str(segment.get("prompt") or "").strip() and old != asset["id"]:
                segment["prompt_status"] = "stale"
            candidate["adopted_asset_id"] = asset["id"]
            self.store.save(project)
            return {"asset": saved_asset, "candidate": candidate, "segment": segment}
