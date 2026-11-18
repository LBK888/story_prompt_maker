# Your first video with a cloud GPU

[台灣繁體中文](cloud-quickstart.zh-TW.md)

Your Windows PC runs the storyboard editor, previews and final merge. OpenRouter writes prompts and GPUtw runs ComfyUI. Finish the first two steps before renting GPU time.

## 1. Install and launch on Windows

Install Git for Windows and Python 3.12 with the `py` launcher. Run the following in PowerShell. This setup installs Python packages and FFmpeg and works on a PC without an NVIDIA GPU. Alternatively, select **Code → Download ZIP** on GitHub, extract it, open PowerShell in the folder containing `run.py`, and start at line three.

```powershell
git clone --depth 1 https://github.com/LBK888/story_prompt_maker.git
cd story_prompt_maker
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\install_runtime.ps1 -FfmpegOnly
.\.venv\Scripts\python.exe run.py
```

Open the [local app](http://127.0.0.1:7865) and keep the terminal open. On subsequent launches run the last command in the repository folder.

## 2. Generate a storyboard with OpenRouter

1. Sign in to OpenRouter and create and copy an API key. [OpenRouter keys](https://openrouter.ai/settings/keys)
2. On the home page choose **模型設定** (Model Settings) → **連線方式** (Provider) → **OpenRouter API**. Keep `https://openrouter.ai/api/v1`, paste the key and save with **儲存模型設定**. The default **自動選擇免費模型 · openrouter/free** lets OpenRouter select an available free model capable of handling the request. Select it once if you previously saved a particular model. The key stays in the local `.env` file. [Free model routing](https://openrouter.ai/openrouter/free)
3. Create a project with a one-sentence story, such as “After the rain, an orange cat chases a leaf down an alley.” Set the total and segment lengths to four seconds, save, and click **產生／重建t2v影片藍圖**. A storyboard card confirms that writing works. If OpenRouter returns HTTP 429, wait before retrying.

## 3. Rent the GPU

1. Verify your GPUtw account and add credit. In Windows PowerShell run `ssh-keygen -t ed25519` or reuse an existing key. Run `Get-Content "$env:USERPROFILE/.ssh/id_ed25519.pub"` and paste the public key line into **Dashboard → SSH Keys**. [GPUtw SSH guide](https://docs.gputw.ai/en/docs/ssh-access)
2. Under **Deploy**, select an available GPU and check the hourly rate. A high-VRAM Blackwell card such as an RTX PRO 6000 Blackwell is a suggested starting point. The five weights total about 43 GB. Allow at least 60 GB in Vault. Actual VRAM and system RAM needs depend on clip dimensions and duration. Choose an SSH-enabled **PyTorch/Jupyter development image** with CUDA PyTorch, Python 3.11 or newer, apt and the CUDA compiler `nvcc` (CUDA 12.8 or newer for Blackwell). The installer sets up ComfyUI. [GPUtw deployment](https://docs.gputw.ai/) · [Templates](https://docs.gputw.ai/en/docs/templates) · [SageAttention requirements](https://github.com/thu-ml/SageAttention#installation)
3. Deploy and wait for **Instances → RUNNING**. Copy the SSH destination and port shown on that row. If setting an auto-stop timer, allow time for compiling, downloading and rendering. [GPUtw getting started](https://docs.gputw.ai/)

## 4. Install the cloud stack and connect

Open a second Windows PowerShell in the repository folder. Replace the example destination below with the one from your instance. If its SSH port differs, replace both occurrences of 2222. Run the commands and leave this window open.

```powershell
$gpu = 'pod-YOUR-ID@ssh.gputw.ai'
scp -P 2222 .\scripts\setup_gputw.sh "${gpu}:/vault/setup_gputw.sh"
ssh -p 2222 -o ExitOnForwardFailure=yes -L 127.0.0.1:18188:127.0.0.1:8188 $gpu "bash /vault/setup_gputw.sh"
```

The script installs ComfyUI, KJNodes, SelfLift, VideoHelperSuite, LayerStyle, SageAttention and FFmpeg on the GPU instance. It downloads the five shared weights directly to `/vault/story-h3/models`. Wait for compilation and downloads to finish. When ComfyUI starts listening, open the [connection check](http://127.0.0.1:18188/system_stats) on Windows and look for JSON containing `devices`. [Installer and exact filenames](../scripts/setup_gputw.sh)

[INT8 diffusion model](https://huggingface.co/OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM/tree/main) · [NVFP4 encoder](https://huggingface.co/czb2010/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4/tree/main) · [Video and audio VAEs](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/vae) · [3D latent upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler/blob/27236837051793bc7cccf628e5936c6110361dab/minimax_h3_latent_upscaler_3d_fp16.safetensors)

## 5. Render a clip in the local app

1. Open **連線設定** (Connection Settings) in the project. Set **ComfyUI URL** to `http://127.0.0.1:18188`, clear **與 ComfyUI 共用 GPU** (shared GPU), save and click **測試連線**. **ComfyUI 已連線** confirms that the app reached the server through the SSH tunnel.
2. On the segment card choose **Workflow template** and **H3 任務** as shown below. Click **套用** (Apply). Reapply when switching an existing segment to a new template so its saved snapshot updates.

| First clip | Workflow template | H3 task and inputs |
| --- | --- | --- |
| Text only | `10eros_singularity_t2va_template` | `T2VA`. Enter a prompt. No media uploads needed. |
| Reference appearance and sound | `10eros_singularity_ref2va_template` | `Ref2VA`. Upload an image and audio clip, assign them to `REF_IMG0` and `REF_AUD0`, then click **儲存媒體順序** (Save media order). A built-in character can supply both. |

3. Start with a short segment at low resolution. Click **生成新 prompt** (Generate prompt), review it, then **生成/重製本段影片** (Render segment). Play the MP4 on the segment card to confirm the full path works. Choose **設為 active** for each take you want, then **合併 active takes** to merge.

## Troubleshooting and stopping

- **Missing node or weight**. Read the first error in the cloud terminal. Confirm dependency installation and downloads finished. For compiler or SageAttention errors, use a development image with a CUDA toolkit compatible with PyTorch. Stop the server before rerunning step 4. Completed weights are reused.
- **Connection refused or a login page**. Check the instance is RUNNING, the SSH terminal is open and the app uses the local forwarded URL. If local port 18188 is occupied, replace the first 18188 in the SSH command and the port in the app URL with 18189.
- **Out of memory**. Reduce clip duration or resolution. If that still fails, select a GPU with more VRAM.
- **When finished**. Confirm the downloaded video plays locally, choose **Instances → Stop**, and check its status. Closing the browser or SSH can leave GPU billing active. Weights and cloud outputs remain in Vault, which has separate storage charges. When you restart with the same development image, repeat step 4 to rebuild the environment. [Vault persistence and billing](https://docs.gputw.ai/en/docs/vault)
