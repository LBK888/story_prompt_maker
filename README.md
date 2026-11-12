# Vid Auto Prompt v2.0.0

[台灣繁體中文](README_tw.md)

Vid Auto Prompt is a local Windows web app for turning a short story into an editable storyboard, MiniMax H3 prompts, and video clips rendered by ComfyUI. You can review every segment, attach reference images, audio or video, compare takes, and assemble the selected takes into a final video.

## Install and start

1. On Windows 11, install Python 3.11–3.14 with the `py` launcher and an NVIDIA driver that provides `nvidia-smi`. The included installer checks for an NVIDIA GPU.
2. Clone the repository and run the installer from its root.

   ```powershell
   git clone https://github.com/LBK888/story_prompt_maker.git
   cd story_prompt_maker
   .\install.bat
   .\start.bat
   ```

3. Open [the local web app](http://127.0.0.1:7865). The app listens on the loopback address by default. The installer creates `.venv/` and downloads SHA-256-verified Windows builds of llama.cpp and FFmpeg into `runtime/`.
4. Select an LLM if you plan to use AI-assisted writing. Start and configure ComfyUI before generating media. You can create a blank storyboard, enter your own prompts and work on the project before either service is ready.

Provide your own GGUF weights for local inference. The clone includes 121 built-in male characters with their media, totaling about 1.58 GiB.

## Configure the writing model

On the home page, open **模型設定** (Model Settings) and select **連線方式** (Provider).

| Provider | Setup |
| --- | --- |
| Local llama.cpp | Place a GGUF in the repository root or `models/`. Click **重新掃描**, pick the model, and select its matching **視覺投影模型** (mmproj) if you want image analysis with a compatible vision model. |
| Ollama | Start Ollama, pull a model, and use `http://127.0.0.1:11434` for a local instance. Select the model from the API list. |
| OpenAI or OpenRouter | Use the provider's prefilled API URL and enter its API key. Click **載入 API 模型清單** and select a model. |
| Custom OpenAI-compatible API | Enter the service's API base URL, typically ending in `/v1`, and its key if required. Load the model list or enter the exact model ID. |

In an API profile, you can type a full ID in **搜尋或輸入模型 ID** and press Enter to select it. Adjust **Context size** for local llama.cpp if needed, then click **儲存模型設定**. Leave the key field blank to reuse the saved key. The app stores keys in the local `.env` file.

## Connect ComfyUI

1. Install and launch a separate, self-hosted ComfyUI instance. Install the custom nodes and model files used by the workflow you intend to run. For a custom workflow, first test it in ComfyUI with real input values and a node that saves the output file.
2. Create or open a project and click **連線設定** (Connection Settings). Set **ComfyUI URL** to the server's base address, such as `http://127.0.0.1:8188` for a local install or `http://192.168.1.50:8188` for a server on your LAN. The app connects to ComfyUI's HTTP API and expects access without authentication. [ComfyUI server routes](https://docs.comfy.org/development/comfyui-server/comms_routes)
3. Keep **與 ComfyUI 共用 GPU** enabled when local llama.cpp and ComfyUI share a GPU. Before a video job, the app can stop the llama.cpp process it launched to release VRAM. Clear the option for separate GPUs or a separate ComfyUI machine. **中斷LLM釋放vram** manually stops local llama.cpp and interrupts any active model operation.
4. Click **儲存**, then **測試連線**. Look for **ComfyUI 已連線**. This checks server reachability. A successful render also requires the workflow's nodes, models, inputs and output settings.

### Prepare a workflow with the keyword editor

Use a bundled template such as `t2va_template.json` with a compatible ComfyUI setup, or prepare your own with the editor.

1. In ComfyUI, open a working workflow and use **File → Export (API)**. The API-format JSON contains node IDs with `class_type` and `inputs`. [ComfyUI API export example](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/basic_api_example.py)
2. Open [ComfyUI Workflow 關鍵字編輯器](ComfyUI%20Workflow%20關鍵字編輯器.html) from your local clone in a browser. Click **開啟 JSON** and load the exported file. Use **程式碼** or **樹狀** view to inspect the workflow. The toolbar also supports formatting, undo, redo, and search and replace.
3. Select a row under **可替代欄位**. In **替換為主程式 TOKEN**, choose the matching token and click **套用**. If the input is missing from the suggested rows, switch the filter to **全部** or **未知對應**.
4. Set the video prompt field to `[[PROMPT]]`, the save node's filename prefix to `[[OUTPUT_PREFIX]]`, and any inputs you want the app to supply, such as `[[SEED]]`, `[[DURATION]]`, `[[WIDTH]]` and `[[HEIGHT]]`. Media inputs use tokens such as `[[FIRST_FRAME]]`, `[[LAST_FRAME]]`, `[[REF_IMG0]]`, `[[REF_AUD0]]`, `[[REF_VID0]]` and `[[Drive_AUD]]`. Each placeholder occupies the entire JSON value. Repeated tokens receive the same value. [Full token reference](workflow_templates/README.md)
5. Click **下載** and save the JSON under `workflow_templates/`. To show usage notes beside the workflow picker, enter a **Template note**, click **下載 note**, and place the resulting `*.note.txt` beside the JSON with the same base name. The note travels with the project's template snapshot.
6. Reload the project page and apply the template to a segment. After updating a template file, apply it again to replace the saved snapshot.

## Create a storyboard and generate video

### 1. Enter the story and choose references

- On the home page, enter **專案名稱** and **故事草稿**, then click **建立專案**.
- Under **故事與全片設定**, choose the minimum total duration (4–600 seconds), preferred segment length (4–25 seconds, default 15), aspect ratio, resolution, expansion level and visual style. **Strict**, **Balanced** and **Creative** control how freely the planner expands the source. Allow invented dialogue or narration with **創作對白** or **創作旁白** and set the language for each. Click **儲存設定**.
- Under **參考影音圖檔**, drag in files or click the upload area. Click a preview to inspect the original. Set each asset's roles, character name, notes and optional comma-separated segment IDs. A blank binding makes the asset available throughout the project. If you add files directly under `projects/<project-id>/ref_img/`, click **重新掃描** to load them.
- **選取內建角色** opens the character picker. Search the name, profile or sample line, filter by category or voice availability, preview portraits and listen to sample audio. Select up to 30 characters and click **加入所選角色**. Each import creates one combined character asset. Its turnaround and voice fill the workflow's media slots, while its headshot appears in previews. Repeated imports reuse existing assets.

To add your own local characters, use `character_template/<category>_<name>/` with `turnaround.png` and `voice.wav` for import. Add `headshot.png`, `profile.txt` and `line.txt` for the portrait, description and sample line. The picker skips folders beginning with `_` or `.`.

### 2. Plan and review the segments

- Click **產生／重建t2v影片藍圖** for LLM-assisted planning. The planner uses the roles and notes you assigned and treats the total duration as a minimum.
- Click **產生空白分鏡** to make a timed skeleton without calling the LLM. This is available on projects with zero segments. On each card, use **在後方新增空白片段** or **刪除片段** to change the structure.
- Expand **編輯本段分鏡與對白** to edit the duration, summary, shot JSON and dialogue/narration JSON. Keep the JSON fields as arrays and click **儲存本段變更**. A storyboard change can mark its prompt stale. The app estimates speech timing locally. For on-screen dialogue, preserve the exact wording and speaker IDs and plan a close shot of the speaker.

### 3. Assign each segment's workflow and inputs

1. Choose **Workflow template** and **H3 任務** on the segment card and click **套用**. For a first test without references, use a text-to-video workflow with `T2VA`. For image conditioning, use a matching `I2VA` or `FL2VA` workflow. `L2VA` and `Ref2VA` are also available with workflows designed for those modes.
2. For frame-based workflows, select **起始幀** and, for `FL2VA`, **結束幀** as required by the template. You can use **承接前段末幀** with an `I2VA` or `FL2VA` template containing `[[FIRST_FRAME]]`. Leave the start frame empty and ensure the preceding segment has a usable active take before rendering the dependent segment.
3. In **REF參考媒體插槽**, choose assets in the desired order. The labels show the actual image, audio and video mappings. A character can fill both an image slot and an audio slot. `Drive_AUD` reuses the first selected audio or character. Fill all tokens required by the workflow and leave unused rows blank.
4. Use **依檔案順序填入** to fill compatible slots in file order, or **LLM 建議順序** to preview a suggested order. Review the slot labels and click **儲存媒體順序**.

### 4. Write or edit the H3 prompt

- Click **生成新 prompt** on one segment or **創作/重製所有prompts** for a batch. Review the result and any validation messages after up to two automatic repair passes. Generated H3 structure and descriptions use English. Chinese dialogue, lyrics and visible text use Simplified Chinese in generated prompts, with the source text kept in the project.
- Each clip's prompt should restate character appearance, wardrobe, location, lighting, props and the starting action so it can render independently. The app uses the bundled storytelling supplements for T2VA and Ref2VA.
- To write your own prompt, edit the prompt box and click **儲存手動 prompt**. **修改/優化 prompt** sends the current draft and reference selection to the LLM for revision. **插入提示詞模板** opens the snippets in `prompt_templates/`. Choose **插入**, edit the snippet in context, and save the prompt.

### 5. Render, compare and merge takes

1. Click **生成/重製本段影片** for one segment or **依序生成所有影片** to queue the project. The app runs one video job at a time and assigns each run a new seed. Use **影片生成佇列** to check progress or remove a queued item with **×**.
2. Preview the results on the segment card. Click **設為 active** on the output you want to use. Previous takes remain available for comparison.
3. When each segment has an active take, click **合併 active takes**. FFmpeg joins the clips in storyboard order with hard cuts. The final video appears below the segments.

## Generate still images from a segment

The **生成起始幀**, **生成結束幀** and **生成場景圖** tools use the bundled Qwen Image workflows. Install their models and nodes in ComfyUI, then configure the writing LLM, which also supplies a short filename for images made in the UI.

1. Fill the segment summary and provide at least one shot in **鏡頭 JSON** to enable the tools.
2. Open the desired image tool. Leave the references empty for text-to-image, or select up to three images, characters or videos for image editing. For this tool, a video reference contributes a still frame.
3. Click **生成提示詞** or enter your own image prompt. Choose a **Batch（張）** of 1–4 and click **生成圖**.
4. Compare the results and click **採用此圖**. The adopted image becomes a project reference and updates the segment's corresponding frame or scene selection. Check the selected video template's slots before rendering.

## Back up or move a project

- Click **打包下載專案** after generation finishes to save the current edits and export a `.vap.zip` with reference media, character data, template snapshots, takes and final videos.
- On the destination installation, click **匯入專案包** on the home page. Each import gets a new project ID. The archive limit is 20 GiB and 20,000 files. The importer checks the package version, file paths and referenced files. Incomplete jobs can be resubmitted after import. Configure the destination's model and ComfyUI separately.
- **重新命名** changes the project name. **封存**, or **刪除** on a recent-project card, hides it from the home page and retains its files. Projects live under `projects/<project-id>/`, with settings and the storyboard in `project.json`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| ComfyUI connection fails | Start ComfyUI and verify its address and port from the machine running Vid Auto Prompt. Save the URL before clicking **測試連線**. |
| Workflow missing from the picker | Check that the UTF-8 API JSON is in `workflow_templates/`, includes `[[OUTPUT_PREFIX]]`, and uses whole-value placeholders. Reload the project. |
| Generate button disabled | Read the status below the buttons. Apply a workflow, check the prompt status, and fill any missing frame or reference slots. |
| ComfyUI reports a missing node or model | Check the take's error message and the ComfyUI console. Install the matching node or model, or correct the model name in the workflow and reapply it. |
| ComfyUI runs out of VRAM | Check the shared-GPU option for local llama.cpp. Try a smaller resolution, shorter segment or lighter workflow. |
| Merge unavailable | Select a playable active take for every segment and check the installed FFmpeg runtime. |

## Agent CLI, MCP and benchmarking

Run `start_agent.bat` or `.venv\Scripts\python.exe run.py --mode agent` to let an external agent author storyboards, prompts and reference selections. In this mode, the app delegates LLM work to the agent while ComfyUI and FFmpeg still process media. Run `vap.bat --help` for the JSON CLI. Install `requirements-mcp.txt` and run `vap_mcp.py` for the optional stdio MCP adapter. [Agent interface guide](docs/agent-interface.md) · [Bundled agent skill](skills/vid-auto-prompt/SKILL.md)

Run `.venv\Scripts\python.exe -m scripts.benchmark_llm --runs 2 --max-tokens 256` to measure connection latency, time to first token, total time and token rate. Add `--model provider/model-id` to benchmark another model while keeping your saved selection.
