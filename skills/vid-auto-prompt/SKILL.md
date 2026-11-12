---
name: vid-auto-prompt
description: Author storyboards and H3 prompts with the caller's model, then generate images and videos through a local Vid Auto Prompt backend using CLI or MCP.
---

# Vid Auto Prompt

Use the caller's own model for story interpretation, reference inspection, storyboard planning,
H3 prompts, optimization and repair. The backend validates and executes; do not configure an
LLM provider or request server-side generation of text.

The persistent backend must be running: `.venv\Scripts\python.exe run.py --mode agent`.
Run CLI commands from this repository with `vap.bat`, or use
`.venv\Scripts\python.exe vap.py`. `--url URL` precedes the subcommand;
`VID_AUTO_PROMPT_URL` also selects the backend. Check `vap capabilities` first.
CLI success is JSON on stdout; errors are JSON on stderr with nonzero exit status.
MCP exposes the same operations and the `vap://agent-guide` resource.

## Author and execute

1. Create/read the project and set the user's duration, aspect ratio, style and speech choices.
   Import user-supplied media or selected built-in characters. Set only roles supplied by the
   user; request missing role information before making a dependent binding.
2. Get `blueprint` authoring context. Use its messages, dynamic JSON Schema and references to
   generate a blueprint with your own model. Inspect actual images when relevant; use
   `get_reference_image` in MCP or `assets preview` in CLI. Audio/video URLs are also returned.
3. Apply the blueprint with the context's `expected_revision`. In updates, retain every existing
   segment ID. Existing takes, active outputs and template bindings survive plan updates.
   Explicit segment deletion is separate. Content changes mark dependent prompts stale.
4. List/assign appropriate workflow templates and bind exact asset IDs to the reference slots.
   Do this before final prompt authoring because `<Picture N>` refers to the bound slot order.
5. Get `prompt` context for each segment and generate its H3 text. Runtime context includes
   the current H3 rules, storytelling supplement, global bibles and preceding ready prompts.
   Submit with `set_segment_prompt` / `prompt set`; the agent source is validated, unlike a
   human manual override. Normalize/repair errors using your own model. Do not bypass checks
   by changing `prompt_source` or editing project.json.
6. Submit a video job for selected segments or the whole project. Use a new `request_id` for
   each intentional render and reuse the same ID when retrying an identical submission.
   Poll job status, inspect outputs and select the desired take. Merge after at least two active
   videos exist. CLI exit does not stop backend jobs.

For storyboard stills, get `image` context with `image_kind` and up to three selected asset IDs.
Generate the image prompt yourself and submit an image job. `output_title` is optional;
the backend uses a deterministic filename fallback, never an LLM. Inspect candidates and
adopt the chosen image, then refresh prompt context after references change.

For prompt optimization, get `optimize` context with `current_prompt`. Preserve the edited
prompt's meaning and spoken wording. Final submission still enforces the segment's stored
locked dialogue; update the plan explicitly if the user changed that dialogue.

## Revisions, errors and jobs

- Use the revision returned by the latest mutation or refresh the project/context. On
  `revision_conflict`, reread current inputs and reconcile your proposed change before retrying.
- Validation errors include `code`, `path` and `message`. Invalid submissions leave the previous
  content intact. `--validate-only` checks without committing.
- Do not retry unchanged invalid content indefinitely. After three unsuccessful repairs, show
  the remaining errors and request the missing information needed to proceed.
- `queued`, `running`, `submitted` and `connection_error` are nonterminal job states. A
  connection error may recover; do not create a new render just because polling was interrupted.
- Waiting jobs and video prompt IDs survive backend restarts. A crash during an ambiguous
  remote submission, an in-progress image render or a merge is reported as interrupted; inspect
  existing artifacts/ComfyUI before an explicit new request. Never blindly resubmit.
- `job wait` returns exit 3 on timeout and exit 1 for failed/canceled jobs. Polling with MCP
  returns immediately; it does not keep the caller blocked for the render duration.
- Returned artifact/media URLs are relative to the configured backend URL. Local file paths
  refer to the machine running the CLI/MCP process.

For concrete commands and MCP setup, read [the agent interface guide](../../docs/agent-interface.md)
in the source repository. Runtime context is authoritative for current content rules.
