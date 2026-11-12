"""JSON CLI. All work stays in the persistent HTTP backend."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from .agent_client import AgentClient, AgentClientError


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Vid Auto Prompt agent CLI (JSON output; no built-in LLM)")
    root.add_argument("--url", help="Backend URL (or VID_AUTO_PROMPT_URL)")
    commands = root.add_subparsers(dest="command", required=True)

    def command(name, *, project=False, segment=False, revision=False, file=False, parent=commands):
        p = parent.add_parser(name)
        p.add_argument("--json", action="store_true", help="JSON is the default output format")
        if project:
            p.add_argument("--project", required=True)
        if segment:
            p.add_argument("--segment", required=True)
        if revision:
            p.add_argument("--expected-revision", type=int, required=True)
        if file:
            p.add_argument("--file", required=True, help="UTF-8 input file; use - for stdin")
        return p

    def group(name):
        return commands.add_parser(name).add_subparsers(dest="action", required=True)

    command("capabilities")
    project = group("project")
    command("list", parent=project)
    create = command("create", parent=project)
    create.add_argument("--name", required=True)
    create.add_argument("--story", default="")
    command("get", parent=project, project=True)
    command("settings", parent=project, project=True, revision=True, file=True)
    context = command("context", project=True)
    context.add_argument("--task", required=True, choices=("blueprint", "prompt", "optimize", "references", "image"))
    context.add_argument("--segment")
    context.add_argument("--kind", choices=("scene", "first_frame", "last_frame"), default="scene")
    context.add_argument("--asset-ids", nargs="*", default=[])
    context.add_argument("--current-prompt-file")
    blueprint = command("apply", parent=group("blueprint"), project=True, revision=True, file=True)
    blueprint.add_argument("--validate-only", action="store_true")
    prompt = command("set", parent=group("prompt"), project=True, segment=True, revision=True, file=True)
    prompt.add_argument("--validate-only", action="store_true")
    command("bind", parent=group("references"), project=True, segment=True, revision=True, file=True)
    templates = group("templates")
    command("list", parent=templates)
    assign = command("assign", parent=templates, project=True, segment=True, revision=True)
    assign.add_argument("--name", required=True)
    assign.add_argument("--task-type", choices=("T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"))
    assets = group("assets")
    command("list", parent=assets, project=True)
    command("import", parent=assets, project=True, file=True)
    update = command("update", parent=assets, project=True, revision=True, file=True)
    update.add_argument("--asset", required=True)
    preview = command("preview", parent=assets, project=True)
    preview.add_argument("--asset", required=True)
    preview.add_argument("--output", required=True)
    characters = group("characters")
    command("list", parent=characters)
    add = command("add", parent=characters, project=True)
    add.add_argument("--ids", nargs="+", required=True)

    def submission(p):
        p.add_argument("--request-id", required=True, help="Reuse only for retries of the same request")

    generate = command("generate", project=True, revision=True)
    generate.add_argument("--segments", nargs="*", default=[])
    submission(generate)
    image = group("image")
    generate_image = command("generate", parent=image, project=True, segment=True, revision=True, file=True)
    generate_image.add_argument("--kind", choices=("scene", "first_frame", "last_frame"), required=True)
    generate_image.add_argument("--asset-ids", nargs="*", default=[])
    generate_image.add_argument("--count", type=int, choices=range(1, 5), default=1)
    generate_image.add_argument("--name")
    submission(generate_image)
    image_list = command("list", parent=image, project=True, segment=True)
    image_list.add_argument("--kind", choices=("scene", "first_frame", "last_frame"), required=True)
    adopt = command("adopt", parent=image, project=True, segment=True)
    adopt.add_argument("--candidate", required=True)
    jobs = group("job")
    command("list", parent=jobs, project=True)
    for action in ("get", "cancel", "wait"):
        p = command(action, parent=jobs)
        p.add_argument("job_id")
        if action == "wait":
            p.add_argument("--timeout", type=float, default=60)
            p.add_argument("--interval", type=float, default=2)
    command("artifacts", project=True)
    take = command("select", parent=group("take"), project=True, segment=True)
    take.add_argument("--take", required=True)
    take.add_argument("--output-index", type=int, default=0)
    merge = command("merge", project=True, revision=True)
    submission(merge)
    return root


def read_input(path: str, *, as_json: bool = False) -> Any:
    value = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    return json.loads(value) if as_json else value


async def execute(args: argparse.Namespace, client: AgentClient) -> Any:
    command, action = args.command, getattr(args, "action", "")
    project_id = getattr(args, "project", "")
    # IDs in this project use the restricted storage ID alphabet.
    from urllib.parse import quote
    project_id = quote(project_id, safe="")
    base = f"/api/projects/{project_id}"
    agent = f"/api/agent/projects/{project_id}"
    segment = quote(getattr(args, "segment", "") or "", safe="")
    if command == "capabilities":
        return await client.request("GET", "/api/agent/capabilities")
    if command == "project":
        if action == "list":
            return await client.request("GET", "/api/projects")
        if action == "create":
            return await client.request("POST", "/api/projects", json={"name": args.name, "story": args.story})
        if action == "get":
            return await client.request("GET", base)
        return await client.request("PATCH", agent + "/settings", json={"changes": read_input(args.file, as_json=True), "expected_revision": args.expected_revision})
    if command == "context":
        return await client.context(args.project, args.task, segment_id=args.segment, image_kind=args.kind,
                                    asset_ids=args.asset_ids,
                                    current_prompt=read_input(args.current_prompt_file) if args.current_prompt_file else None)
    if command == "blueprint":
        return await client.apply_blueprint(args.project, read_input(args.file, as_json=True), args.expected_revision, args.validate_only)
    if command == "prompt":
        return await client.set_prompt(args.project, args.segment, read_input(args.file), args.expected_revision, args.validate_only)
    if command == "references":
        return await client.request("PUT", agent + f"/segments/{segment}/references", json={"changes": read_input(args.file, as_json=True), "expected_revision": args.expected_revision})
    if command == "templates":
        if action == "list":
            return await client.request("GET", "/api/templates")
        return await client.request("PUT", agent + f"/segments/{segment}/template", json={"template_name": args.name, "task_type": args.task_type, "expected_revision": args.expected_revision})
    if command == "assets":
        if action == "list":
            return (await client.request("GET", base))["assets"]
        if action == "import":
            return await client.import_asset(args.project, args.file)
        asset_id = quote(args.asset, safe="")
        if action == "update":
            return await client.request("PATCH", agent + f"/assets/{asset_id}", json={"changes": read_input(args.file, as_json=True), "expected_revision": args.expected_revision})
        response = await client.response("GET", agent + f"/assets/{asset_id}/preview")
        destination = Path(args.output)
        destination.write_bytes(response.content)
        return {"path": str(destination.resolve()), "mime_type": response.headers.get("content-type")}
    if command == "characters":
        if action == "list":
            return await client.request("GET", "/api/characters")
        return await client.request("POST", base + "/characters", json={"ids": args.ids})
    if command == "generate":
        return await client.submit(args.project, args.expected_revision, args.request_id, segment_ids=args.segments)
    if command == "image":
        if action == "generate":
            return await client.submit(args.project, args.expected_revision, args.request_id, "image",
                                        segment_id=args.segment, image_kind=args.kind, asset_ids=args.asset_ids,
                                        prompt=read_input(args.file), batch=args.count, output_title=args.name)
        if action == "list":
            return await client.request("GET", base + f"/segments/{segment}/images", params={"kind": args.kind})
        return await client.request("POST", base + f"/segments/{segment}/images/{quote(args.candidate, safe='')}/adopt")
    if command == "job":
        if action == "list":
            return await client.request("GET", base + "/jobs", params={"active_only": False})
        job_id = quote(args.job_id, safe="")
        if action == "cancel":
            return await client.request("DELETE", f"/api/jobs/{job_id}")
        deadline = time.monotonic() + max(0, getattr(args, "timeout", 0))
        while True:
            job = await client.request("GET", f"/api/agent/jobs/{job_id}")
            if action != "wait" or job["status"] in {"completed", "failed", "canceled"}:
                return job
            if time.monotonic() >= deadline:
                return {**job, "wait_timed_out": True}
            await asyncio.sleep(min(max(args.interval, 0.1), max(0, deadline - time.monotonic())))
    if command == "artifacts":
        return await client.request("GET", agent + "/artifacts")
    if command == "take":
        return await client.request("POST", base + f"/segments/{segment}/takes/{quote(args.take, safe='')}/select", json={"output_index": args.output_index})
    if command == "merge":
        return await client.submit(args.project, args.expected_revision, args.request_id, "merge")
    raise ValueError(f"Unknown command: {command}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(execute(args, AgentClient(args.url)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("wait_timed_out"):
            return 3
        if args.command == "job" and isinstance(result, dict) and result.get("status") in {"failed", "canceled"}:
            return 1
        return 0
    except (AgentClientError, OSError, ValueError) as exc:
        detail = exc.detail if isinstance(exc, AgentClientError) else {"code": "invalid_input", "message": str(exc)}
        print(json.dumps({"error": detail}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
