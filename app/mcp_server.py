"""Optional stdio MCP adapter. Start the HTTP backend separately."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from .agent_client import AgentClient


def create_server(client: AgentClient | None = None):
    try:
        from mcp.server.fastmcp import FastMCP, Image
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise RuntimeError("Install MCP dependencies: .venv\\Scripts\\python.exe -m pip install -r requirements-mcp.txt") from exc

    client = client or AgentClient()
    server = FastMCP("Vid Auto Prompt", instructions=(
        "Use your own model to author storyboards and prompts. This server never requests LLM sampling. "
        "Get authoring context, inspect references, submit results with expected_revision, repair validation errors. "
        "Rendering runs in the separately started persistent backend; poll jobs for results. "
        "Reuse request_id only when retrying the same render request. Reference roles are supplied by users."
    ))
    read_only = ToolAnnotations(readOnlyHint=True)

    def base(project_id: str, agent: bool = False):
        return f"/api/{'agent/' if agent else ''}projects/{quote(project_id, safe='')}"

    @server.tool(annotations=read_only)
    async def get_capabilities() -> dict[str, Any]:
        """Check backend connectivity, agent mode and supported tasks without contacting an LLM."""
        return await client.request("GET", "/api/agent/capabilities")

    @server.tool(annotations=read_only)
    async def list_projects() -> list[dict[str, Any]]:
        """List local projects, their settings and current revisions."""
        return await client.request("GET", "/api/projects")

    @server.tool()
    async def create_project(name: str, story: str = "") -> dict[str, Any]:
        """Create a project. Use update_settings before obtaining blueprint context."""
        return await client.request("POST", "/api/projects", json={"name": name, "story": story})

    @server.tool(annotations=read_only)
    async def get_project(project_id: str) -> dict[str, Any]:
        """Read settings, assets, segments, takes and the latest revision."""
        return await client.request("GET", base(project_id))

    @server.tool()
    async def update_settings(project_id: str, expected_revision: int, changes: dict[str, Any]) -> dict[str, Any]:
        """Set story, duration, style, aspect ratio and dialogue choices; invalidates dependent prompts."""
        return await client.request("PATCH", base(project_id, True) + "/settings",
                                    json={"expected_revision": expected_revision, "changes": changes})

    @server.tool(annotations=read_only)
    async def get_authoring_context(project_id: str,
                                     task: Literal["blueprint", "prompt", "optimize", "references", "image"],
                                     segment_id: str | None = None,
                                     image_kind: Literal["scene", "first_frame", "last_frame"] = "scene",
                                     asset_ids: list[str] | None = None,
                                     current_prompt: str | None = None) -> dict[str, Any]:
        """Get runtime rules, schema, reference aliases and revision. Generate the requested content with your own model. Optimize needs current_prompt."""
        return await client.context(project_id, task, segment_id=segment_id, image_kind=image_kind,
                                     asset_ids=asset_ids or [], current_prompt=current_prompt)

    @server.tool()
    async def apply_blueprint(project_id: str, blueprint: dict[str, Any], expected_revision: int,
                               validate_only: bool = False) -> dict[str, Any]:
        """Validate/save caller-authored blueprint. Updates must preserve existing segment IDs; takes and bindings are retained."""
        return await client.apply_blueprint(project_id, blueprint, expected_revision, validate_only)

    @server.tool()
    async def set_segment_prompt(project_id: str, segment_id: str, prompt: str, expected_revision: int,
                                  validate_only: bool = False) -> dict[str, Any]:
        """Validate/save caller-authored H3 prompt. Invalid prompts return repairable errors without replacing the previous prompt."""
        return await client.set_prompt(project_id, segment_id, prompt, expected_revision, validate_only)

    @server.tool(annotations=read_only)
    async def list_templates() -> dict[str, Any]:
        """List valid ComfyUI workflow templates, notes and required placeholders."""
        return await client.request("GET", "/api/templates")

    @server.tool()
    async def assign_template(project_id: str, segment_id: str, template_name: str,
                               expected_revision: int,
                               task_type: Literal["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"] | None = None) -> dict[str, Any]:
        """Snapshot a workflow for a segment before authoring its final prompt."""
        return await client.request("PUT", base(project_id, True) + f"/segments/{quote(segment_id, safe='')}/template",
                                    json={"template_name": template_name, "expected_revision": expected_revision, "task_type": task_type})

    @server.tool()
    async def bind_references(project_id: str, segment_id: str, expected_revision: int,
                               changes: dict[str, Any]) -> dict[str, Any]:
        """Bind image_order and optional first_frame_asset_id, last_frame_asset_id, scene_ref_asset_id. Use explicit asset IDs; do not infer user roles."""
        return await client.request("PUT", base(project_id, True) + f"/segments/{quote(segment_id, safe='')}/references",
                                    json={"changes": changes, "expected_revision": expected_revision})

    @server.tool()
    async def import_asset(project_id: str, local_path: str) -> dict[str, Any]:
        """Upload a local media file accessible to this stdio process. Set user-provided roles separately with update_asset."""
        return await client.import_asset(project_id, local_path)

    @server.tool()
    async def update_asset(project_id: str, asset_id: str, expected_revision: int,
                             changes: dict[str, Any]) -> dict[str, Any]:
        """Set user-assigned roles, notes, character_name and bindings for an imported asset."""
        return await client.request("PATCH", base(project_id, True) + f"/assets/{quote(asset_id, safe='')}",
                                    json={"changes": changes, "expected_revision": expected_revision})

    @server.tool(annotations=read_only)
    async def get_reference_image(project_id: str, asset_id: str) -> Any:
        """Return a resized reference image/character turnaround to the caller's vision model."""
        response = await client.response("GET", base(project_id, True) + f"/assets/{quote(asset_id, safe='')}/preview")
        return Image(data=response.content, format="jpeg")

    @server.tool(annotations=read_only)
    async def list_characters() -> list[dict[str, Any]]:
        """Browse the built-in character catalog. Choose characters according to the user's request."""
        return await client.request("GET", "/api/characters")

    @server.tool()
    async def add_characters(project_id: str, ids: list[str]) -> dict[str, Any]:
        """Import selected built-in characters and their voice/turnaround media into a project."""
        return await client.request("POST", base(project_id) + "/characters", json={"ids": ids})

    @server.tool()
    async def submit_generation(project_id: str, expected_revision: int, request_id: str,
                                 kind: Literal["video", "image", "merge"] = "video",
                                 segment_ids: list[str] | None = None, segment_id: str | None = None,
                                 image_kind: Literal["scene", "first_frame", "last_frame"] = "scene",
                                 asset_ids: list[str] | None = None, prompt: str = "", batch: int = 1,
                                 output_title: str | None = None) -> dict[str, Any]:
        """Enqueue a durable job and return immediately. Video uses saved prompts; image requires caller prompt, segment_id and image_kind. Merge needs two active videos. Reuse request_id for identical retries only."""
        return await client.submit(project_id, expected_revision, request_id, kind,
                                    segment_ids=segment_ids or [], segment_id=segment_id, image_kind=image_kind,
                                    asset_ids=asset_ids or [], prompt=prompt, batch=batch, output_title=output_title)

    @server.tool(annotations=read_only)
    async def get_job(job_id: str) -> dict[str, Any]:
        """Poll a job for status, errors and image/merge result. Does not wait for rendering."""
        return await client.request("GET", f"/api/agent/jobs/{quote(job_id, safe='')}")

    @server.tool(annotations=read_only)
    async def list_jobs(project_id: str) -> dict[str, Any]:
        """List queued, active and finished project jobs, including restored jobs."""
        return await client.request("GET", base(project_id) + "/jobs", params={"active_only": False})

    @server.tool()
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a waiting job. Running ComfyUI jobs may refuse cancellation."""
        return await client.request("DELETE", f"/api/jobs/{quote(job_id, safe='')}")

    @server.tool(annotations=read_only)
    async def get_artifacts(project_id: str) -> dict[str, Any]:
        """Get takes, image candidates and final outputs with downloadable backend-relative URLs."""
        return await client.request("GET", base(project_id, True) + "/artifacts")

    @server.tool()
    async def select_take(project_id: str, segment_id: str, take_id: str,
                            output_index: int = 0) -> dict[str, Any]:
        """Select a completed video output for continuity and final merging."""
        return await client.request("POST", base(project_id) + f"/segments/{quote(segment_id, safe='')}/takes/{quote(take_id, safe='')}/select",
                                    json={"output_index": output_index})

    @server.tool()
    async def adopt_image(project_id: str, segment_id: str, candidate_id: str) -> dict[str, Any]:
        """Adopt a generated image candidate as the segment's frame/scene reference; refresh context afterwards."""
        return await client.request("POST", base(project_id) + f"/segments/{quote(segment_id, safe='')}/images/{quote(candidate_id, safe='')}/adopt")

    @server.resource("vap://agent-guide")
    def guide() -> str:
        return (Path(__file__).resolve().parent.parent / "skills" / "vid-auto-prompt" / "SKILL.md").read_text(encoding="utf-8")

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Vid Auto Prompt stdio MCP adapter")
    parser.add_argument("--url", help="Backend URL (or VID_AUTO_PROMPT_URL)")
    args = parser.parse_args()
    try:
        server = create_server(AgentClient(args.url))
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
