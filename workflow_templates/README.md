# Workflow templates

Place user-maintained ComfyUI API-format workflow JSON files here. The app does not inspect node IDs or `class_type` values. A placeholder must be the complete JSON value:

```json
{
  "36": {"inputs": {"prompt": "[[PROMPT]]"}},
  "9": {"inputs": {"noise_seed": "[[SEED]]"}},
  "14": {"inputs": {"value": "[[DURATION]]"}},
  "12": {"inputs": {"filename_prefix": "[[OUTPUT_PREFIX]]"}}
}
```

Supported placeholders:

- `[[PROMPT]]`
- `[[SEED]]`
- `[[DURATION]]`
- `[[WIDTH]]`, `[[HEIGHT]]`, `[[ASPECT_RATIO]]`
- `[[FIRST_FRAME]]`, `[[LAST_FRAME]]`
- `[[REF_IMG0]]`, `[[REF_IMG1]]`, … (images)
- `[[REF_AUD0]]`, `[[REF_AUD1]]`, … (reference audio)
- `[[REF_VID0]]`, `[[REF_VID1]]`, … (reference video)
- `[[Drive_AUD]]` (first selected audio or built-in character)
- `[[OUTPUT_PREFIX]]` (required at least once)
- `[[TASK_TYPE]]`, `[[PROJECT_ID]]`, `[[SEGMENT_ID]]`

Placeholders may occur multiple times; every occurrence receives the same typed value. Numeric media suffixes may start at 0 or 1. A selected character fills matching image/audio slots using its turnaround and voice; a portrait is preview-only. The app replaces parsed JSON values with the proper string or numeric type, serializes the result again, and refuses submission if a marker remains.

Optional template notes use a separate UTF-8 plain-text file next to the JSON: `example_template.json` pairs with `example_template.note.txt`. The standalone workflow keyword editor has a note field and separate download buttons for these two files. Keep their base names identical when copying them into `workflow_templates/`. The app shows a nonempty note when the template is selected and stores it with the project snapshot. The note file is never parsed as a ComfyUI node, searched for tokens, or sent to ComfyUI. Existing workflows, including those with a real `note` node, need no changes.


`AAIIref2AV_1.5x_template.json` is the marked two-pass version of the supplied original. Both conditioning passes share prompt, media and task values; noise nodes share the seed. Output dimensions feed the first pass and the existing 1.5× upscaler remains unchanged. The extra drive-audio loader receives the same file as the first selected audio-capable asset. The original unmarked JSON is retained.
