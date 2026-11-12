from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .comfy_client import ComfyClient, ComfyUIError
from .config import AppConfig
from .domain import new_id, utc_now
from .events import EventBroker
from .llama_service import LlamaServerManager
from .media import safe_remote_filename
from .character_assets import asset_filename
from .reference_order import resolve_references
from .storage import ProjectStore
from .takes import _refresh_segment_status
from .template_catalog import TemplateCatalog
from .template_engine import REF_TOKEN_PATTERN, render_template
from .timing import dimensions_for_ratio
from .video import extract_last_frame, probe_duration


class GenerationError(RuntimeError):
    pass


class GenerationManager:
    def __init__(
        self,
        store: ProjectStore,
        templates: TemplateCatalog,
        llama: LlamaServerManager,
        config: AppConfig,
        events: EventBroker,
    ) -> None:
        self.store = store
        self.templates = templates
        self.llama = llama
        self.config = config
        self.events = events
        self._pending: deque[str] = deque()
        self._worker_task: asyncio.Task[None] | None = None
        self._sequence = 0
        self.jobs: dict[str, dict[str, Any]] = {}
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._project_lock = asyncio.Lock()
        self._retry_at: dict[str, float] = {}
        self.operations: dict[str, Any] = {}

    def _persist_jobs(self, project_id: str) -> None:
        if isinstance(self.store, ProjectStore):
            records = [dict(j) for j in self.jobs.values() if j.get("project_id") == project_id]
            self.store.mutate(project_id, lambda p: p.update(generation_jobs=records))

    async def shutdown(self) -> None:
        tasks = [t for t in [self._worker_task, *self._monitors.values()] if t and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def remote_comfy(self) -> bool:
        return (urlparse(self.config.comfy_url).hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}

    def resume_submitted(self, project_id: str | None = None) -> None:
        """Reattach to durable ComfyUI prompt IDs after an app restart."""
        projects = [self.store.load(project_id)] if project_id else self.store.list(include_archived=True)
        for project in projects:
            for saved in project.get("generation_jobs", []):
                if saved["id"] in self.jobs:
                    continue
                job = dict(saved)
                self.jobs[job["id"]] = job
                self._sequence = max(self._sequence, int(job.get("sequence", 0)))
                if job["status"] == "queued":
                    self._pending.append(job["id"])
                elif job["status"] in {"running", "submitted", "connection_error"}:
                    take = next((t for s in project.get("segments", []) for t in s.get("takes", [])
                                 if t.get("job_id") == job["id"] or t.get("id") == job.get("run_id")), None)
                    if take and take.get("prompt_id"):
                        job.update(run_id=take["id"], prompt_id=take["prompt_id"], status=take["status"])
                        if take["status"] == "running":
                            job["status"] = "submitted"
                    else:
                        job.update(status="failed", error="Backend interrupted before a durable result; inspect ComfyUI before explicitly resubmitting", finished_at=utc_now())
            for segment in project.get("segments", []):
                for take in segment.get("takes", []):
                    if take.get("status") != "running" or not take.get("prompt_id"):
                        continue
                    existing = self.jobs.get(take.get("job_id")) or next((j for j in self.jobs.values() if
                                     j.get("project_id") == project["id"] and
                                     (j.get("run_id") == take["id"] or
                                      (j.get("segment_id") == segment["id"] and
                                       j.get("status") == "running" and not j.get("run_id")))), None)
                    job_id = existing["id"] if existing else f"resume_{take['id']}"
                    if job_id not in self.jobs:
                        self._sequence += 1
                        self.jobs[job_id] = {"id": job_id, "project_id": project["id"], "segment_id": segment["id"],
                                             "segment_ids": [segment["id"]], "sequence": self._sequence,
                                             "status": "submitted", "prompt_id": take["prompt_id"],
                                             "run_id": take["id"], "created_at": take.get("created_at"),
                                             "started_at": take.get("created_at"), "finished_at": None,
                                             "error": None, "failures": {}}
                    elif self.jobs[job_id]["status"] == "connection_error":
                        if time.monotonic() < self._retry_at.get(job_id, 0):
                            continue
                        self.jobs[job_id]["status"] = "submitted"
                    if existing:
                        existing["run_id"] = take["id"]
                        existing["prompt_id"] = take["prompt_id"]
                        if existing["status"] == "running":
                            existing["status"] = "submitted"
                    self._start_monitor(job_id)
        if self._pending:
            self._ensure_worker()

    def _start_monitor(self, job_id: str) -> None:
        if job_id not in self._monitors or self._monitors[job_id].done():
            self._monitors[job_id] = asyncio.create_task(self._monitor(job_id))

    def active_job(self, project_id: str) -> dict[str, Any] | None:
        return next(
            (
                job
                for job in self.jobs.values()
                if job.get("project_id") == project_id
                and job.get("status") in {"queued", "running", "submitted", "connection_error"}
            ),
            None,
        )

    def has_active_job(self, project_id: str) -> bool:
        return self.active_job(project_id) is not None

    async def _emit(self, project_id: str, kind: str, message: str, **details: Any) -> None:
        event = {"time": utc_now(), "kind": kind, "message": message, "details": details}
        await self.events.publish(project_id, event)

    def enqueue(self, project_id: str, segment_ids: list[str], *, metadata: dict[str, Any] | None = None) -> list[str]:
        job_ids: list[str] = []
        for segment_id in segment_ids:
            self._sequence += 1
            job_id = new_id("job")
            self.jobs[job_id] = {
                "id": job_id,
                "project_id": project_id,
                "segment_id": segment_id,
                "segment_ids": [segment_id],
                "sequence": self._sequence,
                "status": "queued",
                "error": None,
                "failures": {},
                "created_at": utc_now(),
                "started_at": None,
                "finished_at": None,
                "kind": "video",
                **(metadata or {}),
            }
            self._pending.append(job_id)
            job_ids.append(job_id)
        self._persist_jobs(project_id)
        self._ensure_worker()
        return job_ids

    def enqueue_operation(self, project_id: str, kind: str, payload: dict[str, Any],
                          metadata: dict[str, Any]) -> list[str]:
        if kind not in self.operations:
            raise GenerationError(f"Unsupported operation: {kind}")
        self._sequence += 1
        job_id = new_id("job")
        self.jobs[job_id] = {"id": job_id, "project_id": project_id, "kind": kind,
                             "segment_id": payload.get("segment_id", ""), "segment_ids": [],
                             "payload": payload, "sequence": self._sequence, "status": "queued",
                             "created_at": utc_now(), "started_at": None, "finished_at": None,
                             "error": None, "failures": {}, **metadata}
        self._pending.append(job_id)
        self._persist_jobs(project_id)
        self._ensure_worker()
        return [job_id]

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._run_queue())

    def list_jobs(self, project_id: str, active_only: bool = True) -> list[dict[str, Any]]:
        # A take can be saved before its monitor updates the in-memory job.
        # Reconcile each job independently so completed videos leave the queue
        # on the next poll, even while other prompts are still running.
        active = [job for job in self.jobs.values() if job.get("project_id") == project_id
                  and job.get("run_id") and job.get("status") in {"running", "submitted", "connection_error"}]
        if active and self.store is not None:
            project = self.store.load(project_id)
            takes = {take.get("id"): take for segment in project.get("segments", [])
                     for take in segment.get("takes", [])}
            for job in active:
                take = takes.get(job["run_id"])
                if take and take.get("status") in {"completed", "failed", "canceled"}:
                    job["status"] = take["status"]
                    job["finished_at"] = job.get("finished_at") or utc_now()
        jobs = sorted(
            (job for job in self.jobs.values() if job.get("project_id") == project_id),
            key=lambda job: int(job.get("sequence", 0)),
        )
        if active_only:
            jobs = [job for job in jobs if job.get("status") in {"queued", "running", "submitted", "connection_error"}]
        queue_position = 0
        result: list[dict[str, Any]] = []
        for job in jobs:
            item = dict(job)
            if item.get("status") == "queued":
                queue_position += 1
                item["queue_position"] = queue_position
            else:
                item["queue_position"] = 0
            result.append(item)
        return result

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise GenerationError("Job not found")
        if job.get("status") != "queued":
            raise GenerationError("Only jobs still waiting in the queue can be canceled")
        job["status"] = "canceled"
        job["finished_at"] = utc_now()
        self._persist_jobs(job["project_id"])
        return dict(job)

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise GenerationError("Job not found")
        if job.get("status") == "queued":
            return self.cancel(job_id)
        if job.get("status") not in {"submitted", "connection_error"}:
            raise GenerationError("Only jobs still waiting in the queue can be canceled")
        project_id, segment_id, run_id = job["project_id"], job["segment_id"], job.get("run_id")
        project = self.store.load(project_id)
        segment = self._find_segment(project, segment_id)
        take = next((t for t in segment.get("takes", []) if t.get("id") == run_id), None)
        if take and take.get("status") != "running":
            job["status"] = take["status"]
            job["finished_at"] = utc_now()
            return dict(job)
        prompt_id = job.get("prompt_id") or (take or {}).get("prompt_id")
        if prompt_id:
            client = ComfyClient((take or {}).get("comfy_url") or self.config.comfy_url)
            try:
                history = await client.history(prompt_id)
                if history and (history.get("outputs") or (history.get("status") or {}).get("completed")):
                    raise GenerationError("影片已在 ComfyUI 完成，正在取得結果")
                state, _ = await client.queue_state(prompt_id)
                if state == "running":
                    job["remote_state"] = "running"
                    raise GenerationError("影片正在 ComfyUI 處理中，無法取消")
                if state == "queued":
                    try:
                        await client.delete_queued(prompt_id)
                        state, _ = await client.queue_state(prompt_id)
                    except ComfyUIError as exc:
                        raise GenerationError(f"無法從 ComfyUI 取消排隊工作：{exc}") from exc
                    if state == "running":
                        job["remote_state"] = "running"
                        raise GenerationError("影片已開始在 ComfyUI 處理，無法取消")
                    if state == "queued":
                        raise GenerationError("ComfyUI 仍保留此排隊工作，請稍後再試")
                    history = await client.history(prompt_id)
                    if history and (history.get("outputs") or (history.get("status") or {}).get("completed")):
                        raise GenerationError("影片已在 ComfyUI 完成，正在取得結果")
            except ComfyUIError as exc:
                # The remote job may already have been deleted, or ComfyUI may
                # be temporarily unreachable. Remove the local queue entry.
                job["remote_cancellation_unverified"] = str(exc)
        async with self._project_lock:
            project = self.store.load(project_id)
            segment = self._find_segment(project, segment_id)
            take = next((t for t in segment.get("takes", []) if t.get("id") == run_id), None)
            if take and take.get("status") == "running":
                take["status"] = "canceled"
                segment["status"] = "planned"
                _refresh_segment_status(segment)
                if segment["status"] == "planned" and any(
                    other is not take and other.get("status") in {"submitted", "running"}
                    for other in segment.get("takes", [])
                ):
                    segment["status"] = "running"
                self.store.save(project)
            elif take and take.get("status") == "completed":
                job["status"] = "completed"
                job["finished_at"] = utc_now()
                return dict(job)
        job["status"] = "canceled"
        job["finished_at"] = utc_now()
        monitor = self._monitors.get(job_id)
        if monitor and not monitor.done():
            monitor.cancel()
        self._persist_jobs(project_id)
        return dict(job)

    async def _run_queue(self) -> None:
        stopping = False
        try:
            while self._pending:
                job_id = self._pending.popleft()
                job = self.jobs.get(job_id)
                if job is None or job.get("status") == "canceled":
                    continue
                await self._run_job(job_id)
        except asyncio.CancelledError:
            stopping = True
            raise
        finally:
            self._worker_task = None
            if self._pending and not stopping:
                self._ensure_worker()

    async def _run_job(self, job_id: str) -> None:
        job = self.jobs[job_id]
        segment_id = str(job["segment_id"])
        job["status"] = "running"
        job["started_at"] = utc_now()
        self._persist_jobs(job["project_id"])
        await self._emit(
            str(job["project_id"]),
            "generation_queue_updated",
            f"{segment_id} 已離開等待佇列，正在處理",
            job_id=job_id,
            segment_id=segment_id,
        )
        try:
            if job.get("kind", "video") != "video":
                async with self._project_lock:
                    job["result"] = await self.operations[job["kind"]](job["project_id"], job["payload"])
                job["status"] = "completed"
                return
            defer = False
            if self.remote_comfy():
                project = self.store.load(str(job["project_id"]))
                segment = self._find_segment(project, segment_id)
                defer = not segment.get("continuity_from_previous")
                if not defer:
                    previous = next((s for s in project["segments"] if
                                     int(s.get("index", 0)) == int(segment.get("index", 0)) - 1), None)
                    if previous and not self._active_take(previous):
                        prior_job = next((j for j in reversed(list(self.jobs.values())) if
                                          j.get("project_id") == job["project_id"] and
                                          j.get("segment_id") == previous["id"] and
                                          j["id"] in self._monitors), None)
                        if prior_job:
                            await self._monitors[prior_job["id"]]
            async with self._project_lock:
                if defer:
                    take = await self.generate_segment(str(job["project_id"]), segment_id, defer_wait=True)
                else:
                    take = await self.generate_segment(str(job["project_id"]), segment_id)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
            job["failures"] = {segment_id: str(exc)}
            await self._emit(
                str(job["project_id"]),
                "generation_error",
                str(exc),
                job_id=job_id,
                segment_id=segment_id,
            )
        else:
            if defer:
                job["status"] = "submitted"
                job["prompt_id"] = take["prompt_id"]
                job["run_id"] = take["id"]
                self._start_monitor(job_id)
            else:
                job["status"] = "completed"
        finally:
            if job["status"] != "submitted":
                job["finished_at"] = utc_now()
            self._persist_jobs(job["project_id"])

    def _find_segment(self, project: dict[str, Any], segment_id: str) -> dict[str, Any]:
        segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
        if segment is None:
            raise GenerationError(f"Unknown segment: {segment_id}")
        return segment

    def _active_take(self, segment: dict[str, Any]) -> dict[str, Any] | None:
        active_id = segment.get("active_take_id")
        take = next((take for take in segment.get("takes", []) if take.get("id") == active_id), None)
        if take is None or take.get("status") != "completed" or not take.get("local_path"):
            return None
        outputs = take.get("outputs")
        if isinstance(outputs, list) and take["local_path"] not in outputs:
            return None
        return take

    async def _continuity_frame(self, project_id: str, project: dict[str, Any], segment: dict[str, Any]) -> Path | None:
        if not segment.get("continuity_from_previous") or int(segment.get("index", 1)) <= 1:
            return None
        previous = next(
            (item for item in project["segments"] if int(item.get("index", 0)) == int(segment["index"]) - 1),
            None,
        )
        if previous is None:
            raise GenerationError("Continuity segment has no previous segment")
        take = self._active_take(previous)
        if take is None:
            raise GenerationError(f"Previous segment {previous['id']} has no valid active video")
        local_path = str(take["local_path"])
        video = self.store.project_dir(project_id) / local_path
        if not video.is_file():
            raise GenerationError(f"Previous segment {previous['id']} active video is missing from disk")
        output_fingerprint = hashlib.sha256(local_path.encode("utf-8")).hexdigest()[:12]
        destination = (
            self.store.project_dir(project_id)
            / "extracted_frames"
            / f"{previous['id']}_{take['id']}_{output_fingerprint}.jpg"
        )
        if not destination.exists():
            await extract_last_frame(self.config.ffmpeg_path, video, destination)
        return destination

    async def _upload_inputs(
        self,
        client: ComfyClient,
        project_id: str,
        project: dict[str, Any],
        segment: dict[str, Any],
        tokens: tuple[str, ...],
    ) -> dict[str, str]:
        assets = {item["id"]: item for item in project.get("assets", [])}
        binding = segment["template"]
        remote: dict[str, str] = {}

        uploaded_files = {}

        async def upload_asset(token: str, asset_id: str) -> None:
            asset = assets.get(asset_id)
            if asset is None:
                raise GenerationError(f"Template {token} references missing asset {asset_id}")
            kind = 'audio' if token.startswith('REF_AUD') or token == 'Drive_AUD' else 'video' if token.startswith('REF_VID') else 'image'
            try:
                filename = asset_filename(asset, kind)
            except ValueError as exc:
                raise GenerationError(str(exc)) from exc
            root = (self.store.project_dir(project_id) / 'ref_img').resolve()
            path = (root / filename).resolve()
            if path.parent != root or not path.is_file():
                raise GenerationError(f'角色或參考素材檔案不存在：{filename}')
            if filename not in uploaded_files:
                component = {**asset, 'filename': filename}
                result = await client.upload_input(path, safe_remote_filename(project_id, component))
                uploaded_files[filename] = f"{result.get('subfolder', '')}/{result['name']}".strip('/')
            remote[token] = uploaded_files[filename]

        if "FIRST_FRAME" in tokens:
            first_id = binding.get("first_frame_asset_id")
            if first_id:
                await upload_asset("FIRST_FRAME", first_id)
            else:
                continuity = await self._continuity_frame(project_id, project, segment)
                if continuity is None:
                    raise GenerationError("Template requires [[FIRST_FRAME]], but no start frame or continuity frame is assigned")
                remote_name = f"vid_auto_prompt/{project_id}/continuity/{continuity.name}"
                result = await client.upload_input(continuity, remote_name)
                remote["FIRST_FRAME"] = f"{result.get('subfolder', '')}/{result['name']}".strip("/")
        if "LAST_FRAME" in tokens:
            last_id = binding.get("last_frame_asset_id")
            if not last_id:
                raise GenerationError("Template requires [[LAST_FRAME]], but no end frame is assigned")
            await upload_asset("LAST_FRAME", last_id)

        try:
            mapping = resolve_references({**binding, 'tokens': tokens}, project.get('assets', []), require_complete=True)
        except ValueError as exc:
            raise GenerationError(str(exc)) from exc
        for token, asset_id in mapping.items():
            await upload_asset(token, asset_id)
        return remote

    async def _release_llm_if_needed(self, project_id: str, segment_id: str) -> None:
        if self.config.execution_mode == "agent":
            return
        if not self.config.comfy_shared_gpu:
            await self._emit(
                project_id,
                "generation_progress",
                f"{segment_id}：ComfyUI 使用非共用 GPU，直接準備送件",
                segment_id=segment_id,
            )
            return
        llm_config = getattr(self.llama, "config", None)
        if getattr(llm_config, "llm_provider", "local") != "local":
            await self._emit(
                project_id,
                "generation_progress",
                f"{segment_id}：LLM 使用外部 API，不占用本機 GPU；直接準備送交 ComfyUI",
                segment_id=segment_id,
            )
            return
        await self._emit(
            project_id,
            "generation_progress",
            f"{segment_id}：正在中斷本 app 的 LLM 並釋放其 VRAM",
            segment_id=segment_id,
        )
        await self.llama.stop()
        if self.llama.running_pid() is not None or await self.llama.health():
            raise GenerationError("本 app 的 llama.cpp 未能完全停止，尚未送交 ComfyUI")
        await self._emit(
            project_id,
            "generation_progress",
            f"{segment_id}：LLM 已卸載，正在送交 ComfyUI（不檢查其他程序的 VRAM）",
            segment_id=segment_id,
        )

    async def generate_segment(self, project_id: str, segment_id: str, defer_wait: bool = False) -> dict[str, Any]:
        project = self.store.load(project_id)
        segment = self._find_segment(project, segment_id)
        if segment.get("prompt_status") != "ready":
            raise GenerationError("Segment prompt is missing, invalid, or stale")
        template = self.templates.load_snapshot(project_id, segment)
        client = ComfyClient(self.config.comfy_url)
        await self._emit(project_id, "generation_started", f"已接收 {segment_id}，正在檢查 ComfyUI", segment_id=segment_id)
        segment["status"] = "preparing"
        self.store.save(project)
        try:
            await client.health()
            segment["status"] = "uploading"
            self.store.save(project)
            await self._emit(project_id, "generation_progress", f"{segment_id}：ComfyUI 已連線，正在準備輸入資料", segment_id=segment_id)
            uploaded = await self._upload_inputs(client, project_id, project, segment, template.tokens)
            await self._release_llm_if_needed(project_id, segment_id)
            run_id = new_id("run")
            seed = secrets.randbelow(2**63 - 1)
            settings = project["settings"]
            width, height = dimensions_for_ratio(
                str(settings["aspect_ratio"]), float(settings.get("target_megapixels", 1.0))
            )
            output_prefix = f"vid_auto_prompt/{project_id}/{segment_id}/{run_id}"
            replacements: dict[str, Any] = {
                "PROMPT": segment["prompt"],
                "SEED": seed,
                "DURATION": float(segment["duration_seconds"]),
                "WIDTH": width,
                "HEIGHT": height,
                "ASPECT_RATIO": settings["aspect_ratio"],
                "OUTPUT_PREFIX": output_prefix,
                "TASK_TYPE": segment["task_type"],
                "PROJECT_ID": project_id,
                "SEGMENT_ID": segment_id,
                **uploaded,
            }
            workflow = render_template(template, replacements)
            workflow_file = self.store.project_dir(project_id) / "submitted_workflows" / f"{run_id}.json"
            workflow_file.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            take = {
                "id": run_id,
                "seed": seed,
                "status": "submitted",
                "created_at": utc_now(),
                "template_name": template.name,
                "template_sha256": template.sha256,
                "workflow_path": str(workflow_file.relative_to(self.store.project_dir(project_id))).replace("\\", "/"),
                "prompt": segment["prompt"],
                "prompt_id": None,
                "comfy_url": self.config.comfy_url,
                "output_prefix": output_prefix,
                "outputs": [],
                "local_path": None,
                "actual_duration_seconds": None,
                "error": None,
            }
            active_job = next((j for j in self.jobs.values() if j.get("project_id") == project_id
                               and j.get("segment_id") == segment_id and j.get("status") == "running"), None)
            if active_job:
                take["job_id"] = active_job["id"]
                active_job["run_id"] = run_id
            segment.setdefault("takes", []).append(take)
            segment["status"] = "submitted"
            self.store.save(project)
            prompt_id = await client.submit(workflow)
            take["prompt_id"] = prompt_id
            take["status"] = "running"
            segment["status"] = "running"
            self.store.save(project)
            await self._emit(
                project_id,
                "generation_progress",
                f"{segment_id}：已送交 ComfyUI，正在等待影片生成",
                segment_id=segment_id,
                prompt_id=prompt_id,
            )
            if defer_wait:
                return take
            history = await client.wait(prompt_id)
            outputs = client.outputs(history)
            matches = client.matching_outputs(outputs, output_prefix)
            if not matches:
                raise GenerationError(f"ComfyUI completed but no video matched [[OUTPUT_PREFIX]]={output_prefix}")
            local_outputs: list[str] = []
            for index, output in enumerate(matches, start=1):
                suffix = Path(output.filename).suffix or ".mp4"
                destination = self.store.project_dir(project_id) / "segments" / segment_id / run_id / f"output_{index}{suffix}"
                await client.download(output, destination)
                local_outputs.append(str(destination.relative_to(self.store.project_dir(project_id))).replace("\\", "/"))
            take["outputs"] = local_outputs
            take["status"] = "completed"
            if len(local_outputs) == 1:
                take["local_path"] = local_outputs[0]
                take["actual_duration_seconds"] = await probe_duration(
                    self.config.ffprobe_path,
                    self.store.project_dir(project_id) / local_outputs[0],
                )
                segment["active_take_id"] = run_id
                segment["status"] = "completed"
            else:
                segment["status"] = "needs_take_selection"
            self.store.save(project)
            await self._emit(project_id, "generation_completed", f"Completed {segment_id}", segment_id=segment_id, run_id=run_id)
            return take
        except Exception as exc:
            segment["status"] = "failed"
            if segment.get("takes") and segment["takes"][-1].get("status") in {"submitted", "running"}:
                segment["takes"][-1]["status"] = "failed"
                segment["takes"][-1]["error"] = str(exc)
            self.store.save(project)
            raise

    async def _monitor(self, job_id: str) -> None:
        job = self.jobs[job_id]
        project_id, segment_id, run_id = job["project_id"], job["segment_id"], job["run_id"]
        project = self.store.load(project_id)
        segment = self._find_segment(project, segment_id)
        take = next((t for t in segment.get("takes", []) if t["id"] == run_id), None)
        if take is None or take.get("status") != "running":
            if take and take.get("status") in {"completed", "failed", "canceled"}:
                job["status"] = take["status"]
                job["finished_at"] = utc_now()
            return
        client = ComfyClient(take.get("comfy_url") or self.config.comfy_url)
        output_prefix = take.get("output_prefix") or f"vid_auto_prompt/{project_id}/{segment_id}/{run_id}"
        try:
            missing_checks = 0
            while True:
                history = await client.history(take["prompt_id"])
                if history:
                    status = history.get("status") or {}
                    if status.get("status_str") == "error":
                        raise GenerationError(f"ComfyUI workflow failed: {status.get('messages', [])!r}")
                    if status.get("completed") or history.get("outputs"):
                        break
                state, position = await client.queue_state(take["prompt_id"])
                job["remote_state"] = state
                job["remote_queue_position"] = position
                missing_checks = missing_checks + 1 if state == "unknown" else 0
                if missing_checks >= 5:
                    job["remote_state"] = "missing"
                await asyncio.sleep(3)
            outputs = client.matching_outputs(client.outputs(history), output_prefix)
            if not outputs:
                raise GenerationError(f"ComfyUI completed but no video matched {output_prefix}")
            local_outputs = []
            for index, output in enumerate(outputs, start=1):
                suffix = Path(output.filename).suffix or ".mp4"
                destination = self.store.project_dir(project_id) / "segments" / segment_id / run_id / f"output_{index}{suffix}"
                await client.download(output, destination)
                local_outputs.append(str(destination.relative_to(self.store.project_dir(project_id))).replace("\\", "/"))
            duration = (await probe_duration(self.config.ffprobe_path,
                self.store.project_dir(project_id) / local_outputs[0])) if len(local_outputs) == 1 else None
            async with self._project_lock:
                project = self.store.load(project_id)
                segment = self._find_segment(project, segment_id)
                take = next(t for t in segment["takes"] if t["id"] == run_id)
                take["outputs"] = local_outputs
                take["status"] = "completed"
                if len(local_outputs) == 1:
                    take["local_path"] = local_outputs[0]
                    take["actual_duration_seconds"] = duration
                    segment["active_take_id"] = run_id
                    segment["status"] = "completed"
                else:
                    segment["status"] = "needs_take_selection"
                self.store.save(project)
            job["status"] = "completed"
            await self._emit(project_id, "generation_completed", f"Completed {segment_id}",
                             segment_id=segment_id, run_id=run_id, prompt_id=take["prompt_id"])
        except asyncio.CancelledError:
            raise
        except GenerationError as exc:
            async with self._project_lock:
                project = self.store.load(project_id)
                segment = self._find_segment(project, segment_id)
                take = next((t for t in segment.get("takes", []) if t["id"] == run_id), None)
                if take:
                    take["status"] = "failed"
                    take["error"] = str(exc)
                    segment["status"] = "failed"
                    self.store.save(project)
            job["status"] = "failed"
            job["error"] = str(exc)
            await self._emit(project_id, "generation_error", str(exc), segment_id=segment_id, job_id=job_id)
        except Exception as exc:
            # A transient remote outage is recoverable on the next app start; preserve prompt_id.
            job["status"] = "connection_error"
            job["error"] = str(exc)
            self._retry_at[job_id] = time.monotonic() + 30
            await self._emit(project_id, "generation_error", f"{segment_id}：連線或收件失敗，可重開專案重試：{exc}",
                             segment_id=segment_id, job_id=job_id)
        finally:
            job["finished_at"] = utc_now()
            self._persist_jobs(project_id)
