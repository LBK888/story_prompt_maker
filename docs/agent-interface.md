# Agent interface

The caller's model authors all text and visual decisions. Vid Auto Prompt provides deterministic
validation, asset handling, template rendering, the persistent generation queue, ComfyUI
submission, take management and FFmpeg merge. Neither CLI nor MCP requests model sampling.

## Start

From the repository root, install the normal requirements. MCP is optional:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
# Only for MCP:
.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
.\start_agent.bat
```

`run.py --mode agent` sets the mode for that process, without changing the saved model provider.
Agent mode blocks all built-in inference, model startup, model health probes and model unloading.
No GGUF files, llama.cpp executable or LLM API key is required. ComfyUI must already be running;
FFmpeg/FFprobe must be installed at the paths in `config/app.json` for video processing.
The existing installer supplies these executables; its llama.cpp download is optional to agent use.
An ordinary `start.bat` retains the interactive UI and existing model behavior.

The backend remains bound to its configured local address. CLI and MCP connect to that one backend;
do not run multiple backend processes against the same project directory. Use
`VID_AUTO_PROMPT_PROJECTS_DIR` to select an isolated project root when needed.
`--port PORT` selects a backend port; clients use `--url` or `VID_AUTO_PROMPT_URL`.

## CLI

`vap.bat` calls the repository virtual environment and works from another current directory too.
PowerShell uses `.\vap.bat`; examples below abbreviate it as `vap`.

```text
vap capabilities
vap project create --name "Window" --story "A painter opens a window at sunrise."
vap project get --project PROJECT_ID
vap project settings --project PROJECT_ID --expected-revision REV --file settings.json
vap assets import --project PROJECT_ID --file reference.png
vap assets update --project PROJECT_ID --asset ASSET_ID --expected-revision REV --file asset.json
vap assets preview --project PROJECT_ID --asset ASSET_ID --output preview.jpg
vap characters list
vap characters add --project PROJECT_ID --ids CHARACTER_ID
vap context --project PROJECT_ID --task blueprint
vap blueprint apply --project PROJECT_ID --expected-revision REV --file blueprint.json --validate-only
vap blueprint apply --project PROJECT_ID --expected-revision REV --file blueprint.json
vap templates list
vap templates assign --project PROJECT_ID --segment SEGMENT_ID --name TEMPLATE_NAME --expected-revision REV
vap references bind --project PROJECT_ID --segment SEGMENT_ID --expected-revision REV --file binding.json
vap context --project PROJECT_ID --task prompt --segment SEGMENT_ID
vap prompt set --project PROJECT_ID --segment SEGMENT_ID --expected-revision REV --file prompt.txt
vap generate --project PROJECT_ID --expected-revision REV --request-id render-001
vap job get JOB_ID
vap job wait JOB_ID --timeout 60
vap artifacts --project PROJECT_ID
vap take select --project PROJECT_ID --segment SEGMENT_ID --take TAKE_ID
vap merge --project PROJECT_ID --expected-revision REV --request-id merge-001
```

Use `--file -` for UTF-8 stdin. `--json` is accepted on leaf commands; JSON is always the default.
`--url http://127.0.0.1:7865` must precede the subcommand. All nested command help is available
through `--help`. No client command imports `app.main` or starts a second worker.

Example settings: `{"minimum_duration_seconds": 8, "segment_duration_seconds": 4, "aspect_ratio": "16:9"}`.
Example asset metadata: `{"roles": ["character"], "character_name": "Painter", "notes": "User supplied identity reference"}`.
Example binding: `{"image_order": ["asset_..."], "first_frame_asset_id": null, "last_frame_asset_id": null}`.
Only use roles supplied by the user. Obtain the current blueprint schema through context rather
than copying an old schema from documentation. The current segment contract supports 4–25 seconds.

Image jobs also return immediately:

```text
vap context --project PROJECT_ID --task image --segment SEGMENT_ID --kind scene --asset-ids ASSET_ID
vap image generate --project PROJECT_ID --segment SEGMENT_ID --kind scene --asset-ids ASSET_ID --file image-prompt.txt --name sunrise --count 2 --expected-revision REV --request-id still-001
vap image list --project PROJECT_ID --segment SEGMENT_ID --kind scene
vap image adopt --project PROJECT_ID --segment SEGMENT_ID --candidate IMAGE_ID
```

For optimization: `vap context --project PROJECT_ID --task optimize --segment SEGMENT_ID
--current-prompt-file prompt.txt`. Author the revised text yourself and submit it using `prompt set`.
Locked storyboard dialogue remains enforced on submission.

## MCP

Start the backend first. Configure a stdio server using absolute paths:

```json
{
  "mcpServers": {
    "vid-auto-prompt": {
      "command": "G:/vid_auto_prompt/.venv/Scripts/python.exe",
      "args": ["G:/vid_auto_prompt/vap_mcp.py", "--url", "http://127.0.0.1:7865"]
    }
  }
}
```

Adapt the outer configuration to the host's MCP settings. The adapter uses the official MCP Python
SDK v1 maintenance line, pinned separately so normal web/CLI users do not need MCP dependencies.
It exposes explicit tools for projects, settings, authoring, templates, references, local asset upload,
character selection, image previews, generation, jobs, take selection and artifacts. It returns
structured data, image content for `get_reference_image`, and tool errors for validation failures.
The `vap://agent-guide` resource contains the bundled skill. The server sends no unsolicited model
requests and never needs access to the caller's credentials or hidden reasoning state.

## HTTP contract and recovery

The OpenAPI document at `/openapi.json` documents `/api/agent/*` request models.
Content mutations use `expected_revision`; a mismatch returns HTTP 409 with `current_revision`.
Validation failures return HTTP 422 with `detail.code`, `detail.message`, and per-field errors.
Human manual prompt overrides remain available in the UI; agent submission always uses the
validated `agent` source and never claims to be a manual override.

`POST /api/agent/projects/{id}/jobs` takes `request_id`, `expected_revision`, and `kind`
(`video`, `image`, or `merge`). Retrying the same request ID and inputs returns the existing jobs,
including after restart. Changing inputs with that ID returns 409. A new ID means an intentional
new render. The queue records are persisted in the project; content and job data are portable.

Queued work resumes after a backend restart. Submitted video work reconnects using its saved
ComfyUI prompt ID and retains its public job ID. A crash during submission can have an ambiguous
remote outcome: the backend fails such work explicitly instead of risking a duplicate submission.
In-progress image/merge operations are also interrupted rather than automatically rerun. Inspect
artifacts and ComfyUI before creating a replacement request. Keep the backend running during work.

## Verification

Agent tests replace LLM calls with failures and exercise schema validation, revision conflicts,
stale prompts, render deduplication, restart recovery, CLI/MCP adapters and the image/video/merge
pipeline with a simulated ComfyUI. They do not require a model download or spend rendering compute.
Live rendering quality still depends on the configured ComfyUI templates and models.
