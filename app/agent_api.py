"""Agent operations on the application's one persistent backend."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Literal
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .authoring import Authoring, AuthoringError, segment_by_id
from .media import ensure_analysis_copy
from .readiness import active_video_takes, generation_readiness


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mutation(Input):
    expected_revision: StrictInt = Field(ge=0)


class ContextInput(Input):
    task: Literal["blueprint", "prompt", "optimize", "references", "image"]
    segment_id: str | None = None
    image_kind: Literal["scene", "first_frame", "last_frame"] = "scene"
    asset_ids: list[str] = Field(default_factory=list)
    current_prompt: str | None = None


class BlueprintInput(Mutation):
    blueprint: dict[str, Any]
    validate_only: bool = False


class PromptInput(Mutation):
    prompt: str
    validate_only: bool = False


class ChangesInput(Mutation):
    changes: dict[str, Any]


class TemplateInput(Mutation):
    template_name: str
    task_type: Literal["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"] | None = None


class SubmitInput(Mutation):
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    kind: Literal["video", "image", "merge"] = "video"
    segment_ids: list[str] = Field(default_factory=list)
    segment_id: str | None = None
    image_kind: Literal["scene", "first_frame", "last_frame"] = "scene"
    asset_ids: list[str] = Field(default_factory=list)
    prompt: str = ""
    batch: StrictInt = Field(default=1, ge=1, le=4)
    output_title: str | None = None


def install_agent_api(app: FastAPI, runtime: Callable[[], Any]) -> None:
    @app.exception_handler(AuthoringError)
    async def authoring_error(_: Request, exc: AuthoringError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    router = APIRouter(prefix="/api/agent", tags=["agent"])

    def mutable(r: Any, project_id: str) -> None:
        if r.generation.has_active_job(project_id) or r.llama.inference_status().get("busy"):
            raise AuthoringError("project_busy", "Wait for active work before changing project inputs", status=409)

    def checked(r: Any, project_id: str, revision: int) -> dict[str, Any]:
        mutable(r, project_id)
        project = r.store.load(project_id)
        Authoring(r.store).check_revision(project, revision)
        return project

    @router.get("/capabilities")
    async def capabilities():
        r = runtime()
        return {"contract_version": 1, "execution_mode": r.config.execution_mode,
                "inference": "caller", "built_in_llm_enabled": r.config.execution_mode != "agent",
                "tasks": ["blueprint", "prompt", "optimize", "references", "image"],
                "job_kinds": ["video", "image", "merge"]}

    @router.post("/projects/{project_id}/context")
    async def context(project_id: str, payload: ContextInput):
        return Authoring(runtime().store).context(project_id, **payload.model_dump())

    @router.put("/projects/{project_id}/blueprint")
    async def apply_blueprint(project_id: str, payload: BlueprintInput):
        r = runtime()
        mutable(r, project_id)
        return Authoring(r.store).apply_blueprint(project_id, payload.blueprint, payload.expected_revision,
                                                 validate_only=payload.validate_only)

    @router.put("/projects/{project_id}/segments/{segment_id}/prompt")
    async def set_prompt(project_id: str, segment_id: str, payload: PromptInput):
        r = runtime()
        mutable(r, project_id)
        return Authoring(r.store).set_prompt(project_id, segment_id, **payload.model_dump())

    @router.put("/projects/{project_id}/segments/{segment_id}/references")
    async def references(project_id: str, segment_id: str, payload: ChangesInput):
        r = runtime()
        mutable(r, project_id)
        return Authoring(r.store).bind(project_id, segment_id, payload.changes, payload.expected_revision)

    @router.patch("/projects/{project_id}/settings")
    async def settings(project_id: str, payload: ChangesInput):
        r = runtime()
        with r.store._lock:
            checked(r, project_id, payload.expected_revision)
            return await r.update_project_settings(project_id, payload.changes)

    @router.patch("/projects/{project_id}/assets/{asset_id}")
    async def asset_metadata(project_id: str, asset_id: str, payload: ChangesInput):
        r = runtime()
        with r.store._lock:
            checked(r, project_id, payload.expected_revision)
            asset = await r.update_asset(project_id, asset_id, payload.changes)
            return {"asset": asset, "revision": r.store.load(project_id)["revision"]}

    @router.put("/projects/{project_id}/segments/{segment_id}/template")
    async def template(project_id: str, segment_id: str, payload: TemplateInput):
        r = runtime()
        with r.store._lock:
            checked(r, project_id, payload.expected_revision)
            try:
                segment = r.template_catalog.assign(project_id, segment_id, payload.template_name, payload.task_type)
            except (KeyError, FileNotFoundError, ValueError) as exc:
                raise AuthoringError("invalid_template", str(exc)) from exc
            # A different workflow may change reference aliases even if task_type stays the same.
            project = r.store.load(project_id)
            segment = segment_by_id(project, segment_id)
            if segment.get("prompt"):
                segment["prompt_status"] = "stale"
                r.store.save(project)
            return {"segment": segment, "revision": project["revision"]}

    @router.post("/projects/{project_id}/jobs")
    async def submit(project_id: str, payload: SubmitInput):
        r = runtime()
        with r.store._lock:
            project = r.store.load(project_id)
            fingerprint = hashlib.sha256(json.dumps(payload.model_dump(exclude={"request_id", "expected_revision"}),
                                                     sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            r.generation.resume_submitted(project_id)
            prior = [j for j in r.generation.jobs.values() if j.get("project_id") == project_id
                     and j.get("request_id") == payload.request_id]
            if prior:
                if any(j.get("request_fingerprint") != fingerprint for j in prior):
                    raise AuthoringError("request_id_conflict", "This request_id was used for different inputs", status=409)
                return {"jobs": prior, "replayed": True}
            Authoring(r.store).check_revision(project, payload.expected_revision)
            metadata = {"request_id": payload.request_id, "request_fingerprint": fingerprint}
            if payload.kind == "video":
                ids = payload.segment_ids or [s["id"] for s in project["segments"]]
                if len(ids) != len(set(ids)):
                    raise AuthoringError("duplicate_segment_id", "Choose distinct segments")
                segments = [segment_by_id(project, sid) for sid in ids]
                errors = generation_readiness(segments)
                if errors:
                    raise AuthoringError("generation_not_ready", "; ".join(errors))
                # Preserve timeline order for final-frame continuity, regardless of caller order.
                ids = [s["id"] for s in sorted(segments, key=lambda s: s["index"])]
                job_ids = r.generation.enqueue(project_id, ids, metadata=metadata)
            elif payload.kind == "image":
                try:
                    r.segment_images.context(project_id, payload.segment_id, payload.image_kind, payload.asset_ids)
                except ValueError as exc:
                    raise AuthoringError("invalid_image_request", str(exc)) from exc
                if not payload.prompt.strip():
                    raise AuthoringError("prompt_required", "Supply a caller-generated image prompt")
                job_ids = r.generation.enqueue_operation(project_id, "image", payload.model_dump(), metadata)
            else:
                if len(active_video_takes(project["segments"])) < 2:
                    raise AuthoringError("merge_not_ready", "At least two completed active takes are required")
                job_ids = r.generation.enqueue_operation(project_id, "merge", {}, metadata)
            return {"jobs": [r.generation.jobs[jid] for jid in job_ids], "replayed": False}

    @router.get("/jobs/{job_id}")
    async def job(job_id: str):
        r = runtime()
        item = r.generation.jobs.get(job_id)
        if item is None:
            raise HTTPException(404, "Job not found")
        r.generation.resume_submitted(item["project_id"])
        r.generation.list_jobs(item["project_id"], active_only=False)
        return r.generation.jobs[job_id]

    @router.get("/projects/{project_id}/artifacts")
    async def artifacts(project_id: str):
        project = runtime().store.load(project_id)
        def linked(record):
            item = dict(record)
            path = item.get("path") or item.get("local_path")
            if path:
                item["url"] = f"/api/projects/{project_id}/files/{quote(path, safe='/')}"
            return item
        return {"project_id": project_id, "revision": project.get("revision", 0),
                "segments": [{"id": s["id"], "active_take_id": s.get("active_take_id"),
                              "takes": [linked(t) for t in s.get("takes", [])],
                              "images": [linked(c) for c in s.get("image_candidates", [])]}
                             for s in project["segments"]],
                "final_outputs": [linked(o) for o in project.get("final_outputs", [])]}

    @router.get("/projects/{project_id}/assets/{asset_id}/preview")
    async def preview(project_id: str, asset_id: str):
        r = runtime()
        project = r.store.load(project_id)
        asset = next((a for a in project["assets"] if a["id"] == asset_id), None)
        if asset is None or asset.get("media_type") not in {"image", "character"}:
            raise HTTPException(404, "Image or character reference not found")
        return FileResponse(ensure_analysis_copy(r.store, project_id, asset), media_type="image/jpeg")

    async def image_operation(project_id, payload):
        r = runtime()
        return await r.segment_images.generate_batch(
            project_id, payload["segment_id"], payload["image_kind"], payload["asset_ids"],
            payload["prompt"], payload["batch"], output_title=payload.get("output_title"), use_llm=False)

    async def merge_operation(project_id, payload):
        return await runtime().merge_project(project_id)

    runtime().generation.operations.update(image=image_operation, merge=merge_operation)
    app.include_router(router)
