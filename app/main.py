from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .characters import CharacterCatalog, thumbnail
from .project_archive import MAX_ARCHIVE_BYTES, export_project, import_project

from . import __version__
from .blank_storyboard import (
    ExistingStoryboardError,
    StoryboardStructureError,
    add_blank_segment,
    create_blank_storyboard,
    delete_segment,
)
from .chinese import to_simplified
from .comfy_client import ComfyClient
from .config import (
    LLM_API_KEY_ENV,
    LLM_PROVIDERS,
    ROOT,
    AppConfig,
    llm_api_key,
    load_config,
    save_config,
    save_llm_api_key,
)
from .domain import ASPECT_RATIOS, EXPANSION_MODES, REFERENCE_ROLES, STYLE_CATEGORIES, TASK_TYPES, utc_now
from .events import EventBroker
from .generation import GenerationError, GenerationManager
from .llama_service import LlamaServerError, LlamaServerManager
from .media import scan_ref_img
from .media_types import SUPPORTED_MEDIA, media_size_limit, media_type
from .model_catalog import ModelCatalog, PROVIDER_LABELS, fetch_remote_models, validate_base_url
from .planner import PlanningError, StoryPlanner
from .prompt_templates import load_prompt_templates
from .reference_order import bound_reference_asset_ids, reference_slots, bind_references
from .readiness import active_video_takes, generation_readiness
from .segment_images import SegmentImageService
from .storage import ProjectNotFoundError, ProjectStore
from .takes import TakeOutputError, delete_error_record, detach_take_output
from .template_catalog import TemplateCatalog
from .timing import MEGAPIXEL_OPTIONS, dimensions_for_ratio, estimate_speech_seconds, resolution_options
from .video import concatenate, probe_duration


config = load_config()
store = ProjectStore()
events = EventBroker()
llama = LlamaServerManager(config)
template_catalog = TemplateCatalog(store)
planner = StoryPlanner(store, llama)
generation = GenerationManager(store, template_catalog, llama, config, events)
segment_images = SegmentImageService(store, llama, config)
model_catalog = ModelCatalog()
character_catalog = CharacterCatalog(ROOT / 'character_template')


def current_llm_summary() -> dict[str, Any]:
    provider = config.llm_provider
    return {
        "provider": provider,
        "provider_label": PROVIDER_LABELS[provider],
        "model": config.llm_model or config.model_path,
        "base_url": config.llama_url if provider == "local" else config.llm_base_url,
        "api_key_set": bool(llm_api_key(provider)),
        "is_local": provider == "local",
    }


def ensure_storyboard_structure_mutable(project_id: str) -> None:
    if generation.has_active_job(project_id):
        raise HTTPException(409, "Video generation is active; wait before changing segments")
    if llama.inference_status().get("busy"):
        raise HTTPException(409, "LLM generation is active; wait before changing segments")


@asynccontextmanager
async def lifespan(_: FastAPI):
    for directory in (ROOT / "workflow_templates", ROOT / "projects", ROOT / "logs"):
        directory.mkdir(parents=True, exist_ok=True)
    if config.execution_mode != "agent":
        model_catalog.scan()
    generation.resume_submitted()
    yield
    await generation.shutdown()
    await llama.stop()


app = FastAPI(title="Vid Auto Prompt", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


@app.exception_handler(ProjectNotFoundError)
async def project_not_found(_: Request, exc: ProjectNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"Project not found: {exc}"})


@app.exception_handler(LlamaServerError)
async def llama_error(_: Request, exc: LlamaServerError) -> JSONResponse:
    status = 409 if any(word in str(exc).lower() for word in ("busy", "disabled")) else 502
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(PlanningError)
async def planning_error(_: Request, exc: PlanningError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc), "type": type(exc).__name__})


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"projects": store.list(), "version": __version__},
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_page(request: Request, project_id: str) -> HTMLResponse:
    project = store.load(project_id)
    return templates.TemplateResponse(
        request=request,
        name="project.html",
        context={
            "project": project,
            "version": __version__,
            "resolution_options": {
                ratio: resolution_options(ratio) for ratio in ASPECT_RATIOS
            },
        },
    )


@app.get("/api/projects")
async def list_projects(include_archived: bool = False) -> list[dict[str, Any]]:
    return store.list(include_archived=include_archived)


@app.post("/api/projects")
async def create_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return store.create(str(payload.get("name", "")), str(payload.get("story", "")))


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    return template_catalog.check_updates(project_id)


@app.get('/api/characters')
def list_characters():
    return character_catalog.list()


@app.get('/api/characters/{character_id}/files/{filename}')
def character_file(character_id: str, filename: str):
    try:
        return FileResponse(character_catalog.file(character_id, filename))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/characters/{character_id}/thumbnail')
def character_thumbnail(character_id: str, view: str = 'headshot'):
    try:
        path = character_catalog.file(character_id, 'turnaround.png' if view == 'turnaround' else 'headshot.png')
        return Response(thumbnail(str(path), path.stat().st_mtime_ns), media_type='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=3600'})
    except (ValueError, OSError) as exc:
        raise HTTPException(404, '大頭照無法載入') from exc


@app.post('/api/projects/{project_id}/characters')
def add_characters(project_id: str, payload: dict[str, Any] = Body(...)):
    try:
        ids = payload.get('ids')
        if not isinstance(ids, list):
            raise ValueError('請選取角色')
        return character_catalog.add(store, project_id, ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/projects/{project_id}/export')
async def download_project(project_id: str):
    ensure_storyboard_structure_mutable(project_id)
    store.load(project_id)
    with NamedTemporaryFile(delete=False, suffix='.zip') as file:
        path = Path(file.name)
    try:
        await run_in_threadpool(export_project, store, project_id, path)
        return FileResponse(path, media_type='application/zip', filename=f'{project_id}.vap.zip',
                            background=BackgroundTask(path.unlink, missing_ok=True))
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            raise HTTPException(400, str(exc)) from exc
        raise


@app.post('/api/project-import')
async def upload_project(archive: UploadFile = File(...)):
    path = None
    try:
        with NamedTemporaryFile(delete=False, suffix='.zip') as file:
            path = Path(file.name)
            size = 0
            while chunk := await archive.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise HTTPException(413, '專案包超過 20 GB 上限')
                file.write(chunk)
        return await run_in_threadpool(import_project, store, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await archive.close()
        if path:
            path.unlink(missing_ok=True)


@app.patch("/api/projects/{project_id}/settings")
async def update_project_settings(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    allowed = {
        "story", "minimum_duration_seconds", "segment_duration_seconds", "aspect_ratio", "target_megapixels",
        "style_category", "style_notes", "expansion_mode", "official_preset",
        "create_dialogue", "dialogue_language", "create_narration", "narration_language",
        "speech_speed",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(400, f"Unknown settings: {sorted(unknown)}")
    project = store.load(project_id)
    if "minimum_duration_seconds" in payload:
        duration = int(payload["minimum_duration_seconds"])
        if not 4 <= duration <= 600:
            raise HTTPException(400, "Minimum duration must be 4-600 seconds")
        payload["minimum_duration_seconds"] = duration
    if "segment_duration_seconds" in payload:
        duration = int(payload["segment_duration_seconds"])
        if not 4 <= duration <= 25:
            raise HTTPException(400, "Segment duration must be 4-25 seconds")
        payload["segment_duration_seconds"] = duration
    next_aspect_ratio = str(payload.get("aspect_ratio", project["settings"].get("aspect_ratio", "16:9")))
    next_megapixels = float(payload.get("target_megapixels", project["settings"].get("target_megapixels", 1.0)))
    if not any(abs(next_megapixels - option) < 0.0001 for option in MEGAPIXEL_OPTIONS):
        raise HTTPException(400, "Unsupported target megapixels")
    payload["target_megapixels"] = next_megapixels if "target_megapixels" in payload else project["settings"].get("target_megapixels", 1.0)
    dimensions_for_ratio(next_aspect_ratio, next_megapixels)
    if "style_category" in payload and payload["style_category"] not in STYLE_CATEGORIES:
        raise HTTPException(400, "Unsupported style category")
    if "expansion_mode" in payload and payload["expansion_mode"] not in EXPANSION_MODES:
        raise HTTPException(400, "Unsupported expansion mode")
    planning_payload = {key: value for key, value in payload.items() if key != "target_megapixels"}
    changed = any(project["settings"].get(key) != value for key, value in planning_payload.items())
    project["settings"].update(payload)
    if changed and project.get("segments"):
        project["blueprint_status"] = "stale"
        for segment in project["segments"]:
            segment["prompt_status"] = "stale"
    store.save(project)
    return project


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    project = store.load(project_id)
    if "name" in payload:
        project["name"] = str(payload["name"]).strip() or project["name"]
    if "archived" in payload:
        project["archived"] = bool(payload["archived"])
    store.save(project)
    return project


@app.post("/api/projects/{project_id}/assets")
async def upload_asset(project_id: str, image: UploadFile = File(...)) -> dict[str, Any]:
    suffix = Path(image.filename or "").suffix.lower()
    kind = media_type(image.filename or "")
    if suffix not in SUPPORTED_MEDIA or kind is None:
        raise HTTPException(400, "Unsupported media file. Use JPG, PNG, WebP, MP4, MOV, MKV, WebM, AVI, WAV, MP3, FLAC, M4A, AAC or OGG")
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix, dir=store.project_dir(project_id)) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := await image.read(1024 * 1024):
                temporary.write(chunk)
        if temporary_path.stat().st_size == 0:
            raise HTTPException(400, "The uploaded media file is empty")
        limit = media_size_limit(image.filename or "")
        if temporary_path.stat().st_size > limit:
            raise HTTPException(400, f"{kind.title()} exceeds the {limit // (1024 * 1024)} MB limit")
        return store.import_asset(project_id, temporary_path, image.filename or f"media{suffix}")
    finally:
        await image.close()
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


@app.post("/api/projects/{project_id}/assets/scan")
async def rescan_assets(project_id: str) -> dict[str, Any]:
    additions, errors = scan_ref_img(store, project_id)
    return {"additions": additions, "errors": errors, "project": store.load(project_id)}


@app.patch("/api/projects/{project_id}/assets/{asset_id}")
async def update_asset(project_id: str, asset_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    project = store.load(project_id)
    asset = next((item for item in project.get("assets", []) if item.get("id") == asset_id), None)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    if "roles" in payload:
        roles = list(dict.fromkeys(str(item) for item in payload["roles"]))
        invalid = set(roles) - REFERENCE_ROLES
        if invalid:
            raise HTTPException(400, f"Unsupported roles: {sorted(invalid)}")
        asset["roles"] = roles
    for key in ("notes", "character_name"):
        if key in payload:
            asset[key] = str(payload[key])
    if "bindings" in payload:
        asset["bindings"] = list(dict.fromkeys(str(item) for item in payload["bindings"]))
    for segment in project.get("segments", []):
        if asset_id in bound_reference_asset_ids(segment):
            segment["prompt_status"] = "stale"
    store.save(project)
    return asset


@app.delete("/api/projects/{project_id}/assets/{asset_id}")
async def delete_asset(project_id: str, asset_id: str) -> dict[str, Any]:
    try:
        return store.delete_asset(project_id, asset_id)
    except KeyError:
        raise HTTPException(404, "Asset not found") from None


@app.post("/api/projects/{project_id}/blueprint")
async def generate_blueprint(project_id: str) -> dict[str, Any]:
    return await planner.generate_blueprint(project_id)


@app.post("/api/projects/{project_id}/blueprint/blank")
async def generate_blank_blueprint(project_id: str) -> dict[str, Any]:
    ensure_storyboard_structure_mutable(project_id)
    project = store.load(project_id)
    try:
        create_blank_storyboard(project)
    except ExistingStoryboardError as exc:
        raise HTTPException(409, str(exc)) from None
    store.save(project)
    return project


@app.post("/api/projects/{project_id}/segments")
async def create_blank_segment(
    project_id: str,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    ensure_storyboard_structure_mutable(project_id)
    project = store.load(project_id)
    after_segment_id = str(payload["after_segment_id"]) if payload.get("after_segment_id") else None
    try:
        add_blank_segment(project, after_segment_id)
    except StoryboardStructureError as exc:
        raise HTTPException(404, str(exc)) from None
    store.save(project)
    return project


@app.delete("/api/projects/{project_id}/segments/{segment_id}")
async def remove_segment(project_id: str, segment_id: str) -> dict[str, Any]:
    ensure_storyboard_structure_mutable(project_id)
    project = store.load(project_id)
    try:
        delete_segment(project, segment_id)
    except StoryboardStructureError as exc:
        raise HTTPException(404, str(exc)) from None
    store.save(project)
    return project


@app.patch("/api/projects/{project_id}/segments/{segment_id}")
async def update_segment(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    project = store.load(project_id)
    segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
    if segment is None:
        raise HTTPException(404, "Segment not found")
    protected = {"id", "index", "takes", "active_take_id", "template", "reference_asset_ids"}
    if protected.intersection(payload):
        raise HTTPException(400, f"Use a dedicated endpoint for {sorted(protected.intersection(payload))}")
    if "duration_seconds" in payload and not 4 <= int(payload["duration_seconds"]) <= 25:
        raise HTTPException(400, "Segment duration must be 4-25 seconds")
    duration = float(payload.get("duration_seconds", segment["duration_seconds"]))
    if "task_type" in payload and payload["task_type"] not in TASK_TYPES:
        raise HTTPException(400, "Unsupported H3 task type")
    if "shots" in payload:
        shots = payload["shots"]
        if not isinstance(shots, list):
            raise HTTPException(400, "Shots must be a JSON array")
        starts: list[float] = []
        for shot in shots:
            if not isinstance(shot, dict):
                raise HTTPException(400, "Every shot must be a JSON object")
            try:
                starts.append(float(shot["start_seconds"]))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "Every shot needs a numeric start_seconds")
        if starts and (
            starts[0] != 0
            or starts != sorted(set(starts))
            or any(value < 0 or value >= duration for value in starts)
        ):
            raise HTTPException(400, "Shot start times must begin at 0 and increase below the segment duration")
    if "dialogue" in payload:
        if not isinstance(payload["dialogue"], list):
            raise HTTPException(400, "Dialogue must be a JSON array")
        speed = project["settings"].get("speech_speed", "normal")
        for line in payload["dialogue"]:
            if not isinstance(line, dict) or line.get("kind") not in {"dialogue", "voiceover"}:
                raise HTTPException(400, "Dialogue entries need kind=dialogue or voiceover")
            if line.get("language") not in {"Chinese", "English"}:
                raise HTTPException(400, "Dialogue language must be Chinese or English")
            if line.get("language") == "Chinese":
                line["spoken_text"] = to_simplified(str(line.get("spoken_text", "")))
            line["estimated_seconds"] = estimate_speech_seconds(str(line.get("spoken_text", "")), str(speed))
    segment.update(payload)
    segment["prompt_status"] = "stale"
    store.save(project)
    return segment


@app.post("/api/projects/{project_id}/segments/{segment_id}/prompt")
async def generate_prompt(project_id: str, segment_id: str) -> dict[str, Any]:
    return await planner.generate_segment_prompt(project_id, segment_id)


@app.post("/api/projects/{project_id}/segments/{segment_id}/prompt/optimize")
async def optimize_prompt(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    selection = payload.get("reference_selection")
    if selection is not None and not isinstance(selection, dict):
        raise HTTPException(400, "reference_selection must be an object")
    return await planner.optimize_segment_prompt(
        project_id, segment_id, str(payload.get("prompt") or ""), selection
    )


@app.get("/api/prompt-templates")
async def list_prompt_templates() -> dict[str, Any]:
    return load_prompt_templates(ROOT / "prompt_templates")


@app.post("/api/projects/{project_id}/prompts")
async def generate_prompts(project_id: str) -> dict[str, Any]:
    return await planner.generate_all_prompts(project_id)


@app.put("/api/projects/{project_id}/segments/{segment_id}/prompt")
async def save_manual_prompt(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    project = store.load(project_id)
    segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
    if segment is None:
        raise HTTPException(404, "Segment not found")
    prompt = str(payload.get("prompt", ""))
    segment["prompt"] = prompt
    segment["prompt_source"] = "manual"
    segment["prompt_validation_errors"] = []
    segment["prompt_validation_warnings"] = []
    segment["prompt_status"] = "ready"
    store.save(project)
    return segment


@app.get("/api/templates")
async def list_workflow_templates() -> dict[str, Any]:
    valid, invalid = template_catalog.list()
    return {
        "valid": [
            {"name": item.name, "sha256": item.sha256, "tokens": item.tokens, "filename": item.path.name, "note": item.note}
            for item in valid
        ],
        "invalid": invalid,
    }


@app.post("/api/projects/{project_id}/segments/{segment_id}/template")
async def assign_template(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    task_type = str(payload["task_type"]) if payload.get("task_type") is not None else None
    if task_type is not None and task_type not in TASK_TYPES:
        raise HTTPException(400, "Unsupported H3 task type")
    return template_catalog.assign(project_id, segment_id, str(payload.get("name", "")), task_type)


@app.post("/api/projects/{project_id}/templates/apply-all")
async def assign_template_all(project_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return template_catalog.assign_all(project_id, str(payload.get("name", "")))


@app.patch("/api/projects/{project_id}/segments/{segment_id}/template")
async def update_template_binding(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    project = store.load(project_id)
    segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
    if segment is None or not segment.get("template"):
        raise HTTPException(404, "Segment template binding not found")
    previous_asset_ids = bound_reference_asset_ids(segment)
    known_ids = {str(asset.get("id")) for asset in project.get("assets", [])}
    for key in ("first_frame_asset_id", "last_frame_asset_id"):
        if key in payload:
            asset_id = str(payload[key]) if payload[key] else None
            if asset_id and asset_id not in known_ids:
                raise HTTPException(400, f"Unknown reference media asset: {asset_id}")
            segment["template"][key] = asset_id
    if "scene_ref_asset_id" in payload:
        scene_id = str(payload["scene_ref_asset_id"]) if payload["scene_ref_asset_id"] else None
        if scene_id and scene_id not in known_ids:
            raise HTTPException(400, f"Unknown reference media asset: {scene_id}")
        segment["scene_ref_asset_id"] = scene_id
    if "image_order" in payload:
        image_order = [str(item) for item in payload["image_order"]]
        unknown = [asset_id for asset_id in image_order if asset_id not in known_ids]
        if unknown:
            raise HTTPException(400, f"Unknown reference media asset: {', '.join(unknown)}")
        segment["template"]["image_order"] = image_order
    try:
        bind_references(segment['template'], project['assets'], require_complete='image_order' in payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    segment["reference_asset_ids"] = bound_reference_asset_ids(segment)
    if previous_asset_ids != segment["reference_asset_ids"] and str(segment.get("prompt") or "").strip():
        segment["prompt_status"] = "stale"
    store.save(project)
    return segment


@app.post("/api/projects/{project_id}/segments/{segment_id}/reference-order/suggest")
async def suggest_reference_order(project_id: str, segment_id: str) -> dict[str, Any]:
    return await planner.suggest_reference_order(project_id, segment_id)


@app.post("/api/projects/{project_id}/segments/{segment_id}/images/prompt")
async def generate_segment_image_prompt(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, str]:
    try:
        prompt = await segment_images.prompt(project_id, segment_id, str(payload.get("kind", "")), list(payload.get("asset_ids", [])))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"prompt": prompt}


@app.post("/api/projects/{project_id}/segments/{segment_id}/images")
async def generate_segment_image(project_id: str, segment_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await segment_images.generate_batch(project_id, segment_id, str(payload.get("kind", "")),
                                                   list(payload.get("asset_ids", [])), str(payload.get("prompt", "")),
                                                   payload.get("batch", 1))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/segments/{segment_id}/images")
async def list_segment_images(project_id: str, segment_id: str, kind: str) -> dict[str, Any]:
    try:
        return {"candidates": segment_images.candidates(project_id, segment_id, kind)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/segments/{segment_id}/images/{candidate_id}/adopt")
async def adopt_segment_image(project_id: str, segment_id: str, candidate_id: str) -> dict[str, Any]:
    try:
        return segment_images.adopt(project_id, segment_id, candidate_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/segments/{segment_id}/generate")
async def enqueue_segment(project_id: str, segment_id: str) -> dict[str, Any]:
    project = store.load(project_id)
    segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
    if segment is None:
        raise HTTPException(404, "Segment not found")
    errors = generation_readiness([segment])
    if errors:
        raise HTTPException(400, "；".join(errors))
    job_ids = generation.enqueue(project_id, [segment_id])
    return {"jobs": [generation.jobs[job_id] for job_id in job_ids]}


@app.post("/api/projects/{project_id}/generate")
async def enqueue_all(project_id: str) -> dict[str, Any]:
    project = store.load(project_id)
    segments = sorted(project.get("segments", []), key=lambda item: item["index"])
    errors = generation_readiness(segments)
    if errors:
        raise HTTPException(400, "尚未能生成所有影片：" + "；".join(errors))
    segment_ids = [item["id"] for item in segments]
    job_ids = generation.enqueue(project_id, segment_ids)
    return {"jobs": [generation.jobs[job_id] for job_id in job_ids]}


@app.get("/api/projects/{project_id}/jobs")
async def list_generation_jobs(project_id: str, active_only: bool = True) -> dict[str, Any]:
    store.load(project_id)
    generation.resume_submitted(project_id)
    return {"jobs": generation.list_jobs(project_id, active_only=active_only)}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = generation.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str) -> dict[str, Any]:
    try:
        return await generation.cancel_job(job_id)
    except GenerationError as exc:
        status = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status, str(exc)) from None


@app.post("/api/projects/{project_id}/segments/{segment_id}/takes/{take_id}/select")
async def select_take(
    project_id: str,
    segment_id: str,
    take_id: str,
    payload: dict[str, Any] = Body(default={}),
) -> dict[str, Any]:
    project = store.load(project_id)
    segment = next((item for item in project.get("segments", []) if item.get("id") == segment_id), None)
    if segment is None:
        raise HTTPException(404, "Segment not found")
    take = next((item for item in segment.get("takes", []) if item.get("id") == take_id), None)
    if take is None or take.get("status") != "completed":
        raise HTTPException(400, "Take is not completed")
    outputs = take.get("outputs", [])
    output_index = int(payload.get("output_index", 0))
    if not 0 <= output_index < len(outputs):
        raise HTTPException(400, "Invalid output index")
    take["local_path"] = outputs[output_index]
    take["actual_duration_seconds"] = await probe_duration(
        config.ffprobe_path,
        store.project_dir(project_id) / take["local_path"],
    )
    segment["active_take_id"] = take_id
    segment["status"] = "completed"
    for later in project.get("segments", []):
        if int(later.get("index", 0)) > int(segment.get("index", 0)) and later.get("continuity_from_previous"):
            later["continuity_maybe_stale"] = True
    store.save(project)
    return segment


@app.delete("/api/projects/{project_id}/segments/{segment_id}/takes/{take_id}/outputs/{output_index}")
async def remove_take_output(
    project_id: str,
    segment_id: str,
    take_id: str,
    output_index: int,
) -> dict[str, Any]:
    project = store.load(project_id)
    try:
        result = detach_take_output(project, segment_id, take_id, output_index)
    except TakeOutputError as exc:
        status = 404 if str(exc) in {"Segment not found", "Take not found"} else 400
        raise HTTPException(status, str(exc)) from None
    store.save(project)
    return result


@app.delete("/api/projects/{project_id}/segments/{segment_id}/takes/{take_id}/error")
async def remove_take_error(project_id: str, segment_id: str, take_id: str) -> dict[str, Any]:
    ensure_storyboard_structure_mutable(project_id)
    project = store.load(project_id)
    try:
        result = delete_error_record(project, segment_id, take_id)
    except TakeOutputError as exc:
        status = 404 if str(exc) in {"Segment not found", "Take not found"} else 400
        raise HTTPException(status, str(exc)) from None
    store.save(project)
    return result


@app.post("/api/projects/{project_id}/merge")
async def merge_project(project_id: str) -> dict[str, Any]:
    project = store.load(project_id)
    active = active_video_takes(project.get("segments", []))
    if len(active) < 2:
        raise HTTPException(400, "至少需要兩段有效的 active take 影片才能合併")
    videos = [store.project_dir(project_id) / take["local_path"] for _, take in active]
    width, height = dimensions_for_ratio(
        project["settings"]["aspect_ratio"], float(project["settings"].get("target_megapixels", 1.0))
    )
    output_id = f"final_{utc_now().replace(':', '').replace('+', '_')}"
    destination = store.project_dir(project_id) / "final" / f"{output_id}.mp4"
    await concatenate(config.ffmpeg_path, videos, destination, width, height)
    record = {
        "id": output_id,
        "created_at": utc_now(),
        "path": str(destination.relative_to(store.project_dir(project_id))).replace("\\", "/"),
        "actual_duration_seconds": await probe_duration(config.ffprobe_path, destination),
        "segment_take_ids": [take["id"] for _, take in active],
    }
    project.setdefault("final_outputs", []).append(record)
    store.save(project)
    return record


@app.get("/api/projects/{project_id}/files/{relative_path:path}")
async def project_file(project_id: str, relative_path: str) -> FileResponse:
    root = store.project_dir(project_id).resolve()
    target = (root / relative_path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target)


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "comfy_url": config.comfy_url,
        "comfy_shared_gpu": config.comfy_shared_gpu,
        "llama_context": config.llama_context,
        "llama_preflight_errors": llama.preflight(),
        "llm": {
            "provider": config.llm_provider,
            "active": current_llm_summary(),
            "providers": [
                {"id": provider, "label": PROVIDER_LABELS[provider]}
                for provider in LLM_PROVIDERS
            ],
            "profiles": config.llm_profiles,
            "api_key_set": {
                provider: bool(llm_api_key(provider)) for provider in LLM_API_KEY_ENV
            },
            "local_models": model_catalog.local_models,
            "mmproj_models": model_catalog.mmproj_models,
            "mmproj_path": config.mmproj_path,
        },
    }


@app.patch("/api/config")
async def update_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if "comfy_url" in payload:
        url = str(payload["comfy_url"]).strip().rstrip("/")
        if not re.match(r"^https?://", url):
            raise HTTPException(400, "ComfyUI URL must start with http:// or https://")
        config.comfy_url = url
    if "comfy_shared_gpu" in payload:
        config.comfy_shared_gpu = bool(payload["comfy_shared_gpu"])
    if "llm" in payload:
        llm_payload = payload["llm"]
        if not isinstance(llm_payload, dict):
            raise HTTPException(400, "llm 必須是物件")
        provider = str(llm_payload.get("provider", config.llm_provider))
        if provider not in LLM_PROVIDERS:
            raise HTTPException(400, "不支援的 LLM provider")
        current_profile = config.llm_profiles[provider]
        model = str(llm_payload.get("model", current_profile.get("model", ""))).strip()
        base_url = str(llm_payload.get("base_url", current_profile.get("base_url", ""))).strip()
        if provider == "local":
            available = {item["path"] for item in model_catalog.local_models}
            if model not in available:
                raise HTTPException(400, "請從已掃描的 GGUF 模型清單選擇")
            mmproj_path = str(llm_payload.get("mmproj_path", config.mmproj_path)).strip()
            available_mmproj = {item["path"] for item in model_catalog.mmproj_models}
            if mmproj_path and mmproj_path not in available_mmproj:
                raise HTTPException(400, "請從已掃描的 mmproj 清單選擇")
        else:
            if not model:
                raise HTTPException(400, "請選擇或輸入 API 模型")
            try:
                base_url = validate_base_url(base_url)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            mmproj_path = config.mmproj_path
        try:
            llama_context = int(llm_payload.get("llama_context", config.llama_context))
        except (TypeError, ValueError):
            raise HTTPException(400, "Context size 必須是整數") from None
        if not 1024 <= llama_context <= 262144:
            raise HTTPException(400, "Context size 必須介於 1024 到 262144")
        changed = (
            provider != config.llm_provider
            or model != current_profile.get("model", "")
            or base_url != current_profile.get("base_url", "")
            or (provider == "local" and mmproj_path != config.mmproj_path)
        )
        if changed and llama.inference_status().get("busy"):
            raise HTTPException(409, "LLM 正在生成，請完成或中斷後再切換模型")
        if changed and config.llm_provider == "local":
            await llama.stop()
        config.llm_provider = provider
        current_profile.update({"model": model, "base_url": base_url})
        config.llama_context = llama_context
        if provider == "local":
            config.model_path = model
            config.mmproj_path = mmproj_path
        if provider != "local":
            if bool(llm_payload.get("clear_api_key")):
                save_llm_api_key(provider, None)
            elif str(llm_payload.get("api_key", "")).strip():
                save_llm_api_key(provider, str(llm_payload["api_key"]).strip())
    save_config(config)
    return await get_config()


@app.post("/api/llm/models")
async def list_llm_models(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    provider = str(payload.get("provider", config.llm_provider))
    if provider not in LLM_PROVIDERS:
        raise HTTPException(400, "不支援的 LLM provider")
    if provider == "local":
        model_catalog.scan()
        return {
            "models": model_catalog.local_models,
            "mmproj_models": model_catalog.mmproj_models,
        }
    profile = config.llm_profiles[provider]
    base_url = str(payload.get("base_url") or profile.get("base_url") or "")
    api_key = str(payload.get("api_key") or llm_api_key(provider))
    try:
        models = await fetch_remote_models(provider, base_url, api_key)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(502, str(exc)) from None
    return {"models": models}


@app.post("/api/comfy/test")
async def test_comfy() -> dict[str, Any]:
    stats = await ComfyClient(config.comfy_url).health()
    return {"ok": True, "url": config.comfy_url, "stats": stats}


@app.get("/api/llama/status")
async def llama_status() -> dict[str, Any]:
    running_pid = llama.running_pid()
    return {
        **current_llm_summary(),
        "healthy": await llama.health(),
        "running": running_pid is not None,
        "pid": running_pid,
        "preflight_errors": llama.preflight(),
        "log": str(llama.log_path),
        "context": config.llama_context,
        **llama.inference_status(),
    }


@app.post("/api/llama/unload")
async def unload_llama() -> dict[str, Any]:
    status = llama.inference_status()
    was_running = llama.running_pid() is not None
    stopped = await llama.stop()
    return {
        "ok": True,
        "interrupted": bool(status.get("busy")),
        "was_running": was_running,
        "message": "LLM 已中斷並釋放 VRAM" if stopped else "LLM 未在執行，無需釋放 VRAM",
    }


@app.get("/api/projects/{project_id}/events")
async def event_stream(project_id: str) -> StreamingResponse:
    store.load(project_id)
    queue = events.subscribe(project_id)

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(project_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


from .agent_api import install_agent_api

install_agent_api(app, lambda: sys.modules[__name__])
