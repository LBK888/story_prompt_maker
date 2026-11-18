# 第一次用雲端 GPU 生成影片

[English edition](cloud-quickstart.md)

Windows 電腦負責分鏡、預覽及合併影片，OpenRouter 負責寫提示詞，GPUtw 上的 ComfyUI 負責算圖。先在本機完成前兩步，確認能產生分鏡後再開始租 GPU。

## 1. 在 Windows 安裝並開啟專案

安裝 Git for Windows 和含 `py` 啟動器的 Python 3.12。在 PowerShell 貼上下列指令。這條安裝路徑只下載 Python 套件及 FFmpeg，適用於沒有 NVIDIA 顯卡的電腦。也可在 GitHub 選 **Code → Download ZIP**、解壓縮，在含有 `run.py` 的資料夾開啟 PowerShell，從第三行開始執行。

```powershell
git clone --depth 1 https://github.com/LBK888/story_prompt_maker.git
cd story_prompt_maker
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\install_runtime.ps1 -FfmpegOnly
.\.venv\Scripts\python.exe run.py
```

開啟 [本機工作台](http://127.0.0.1:7865)，保留終端機視窗。下次啟動時，在專案目錄執行最後一行即可。

## 2. 用免費模型產生第一份分鏡

1. 登入 OpenRouter，建立並複製一把 API key。[OpenRouter 金鑰管理](https://openrouter.ai/settings/keys)
2. 本機首頁選 **模型設定 → 連線方式 → OpenRouter API**，保留網址 `https://openrouter.ai/api/v1`，貼上金鑰。選用預設的 **自動選擇免費模型 · openrouter/free**，按 **儲存模型設定**。若以前選過特定模型，手動改選一次即可。金鑰存於本機 `.env`。
3. 建立專案，輸入一句故事，例如「雨後的巷子裡，一隻橘貓追著落葉跑」。將全片及片段時長設為 4 秒，儲存後按 **產生／重建t2v影片藍圖**。看到分鏡卡即可進行下一步。免費路由會依請求能力從可用模型中分配，遇到 429 請稍後再試。[免費路由說明](https://openrouter.ai/openrouter/free)

## 3. 在 GPUtw 租一台 GPU

1. 註冊、驗證電子郵件並儲值。在 Windows PowerShell 執行 `ssh-keygen -t ed25519` 建立金鑰；若已有金鑰可沿用。執行 `Get-Content "$env:USERPROFILE/.ssh/id_ed25519.pub"`，將整行公鑰貼到 **Dashboard → SSH Keys**。[GPUtw SSH 說明](https://docs.gputw.ai/en/docs/ssh-access)
2. 開啟 **Deploy**，選 GPU、機器與小時計價。建議從有充足 VRAM 的 Blackwell 卡開始，例如可用的 RTX PRO 6000 Blackwell。五個模型檔共約 43 GB，請預留至少 60 GB Vault 空間。實際 VRAM 和系統 RAM 需求取決於解析度與秒數，這是起始配置建議。選有 SSH、CUDA 版 PyTorch、Python 3.11 或以上、`nvcc`（Blackwell 用 CUDA 12.8 或以上）與 apt 的 **PyTorch/Jupyter 開發環境**。腳本會安裝自己的 ComfyUI。[GPUtw 部署](https://docs.gputw.ai/) · [環境範本](https://docs.gputw.ai/en/docs/templates) · [SageAttention 安裝條件](https://github.com/thu-ml/SageAttention#installation)
3. 確認費率並部署。等 **Instances → RUNNING**，複製該列 SSH 指令中的帳號、主機及連接埠。可設定自動停機，時間須涵蓋首次下載、編譯及試跑。[GPUtw 入門](https://docs.gputw.ai/)

## 4. 一次裝好雲端環境，並建立連線

在 Windows 的專案資料夾另開 PowerShell。把下方 `pod-YOUR-ID@ssh.gputw.ai` 換成剛才複製的目的地。若 SSH 連接埠不同，兩行的 2222 都要換。貼上執行，並保持視窗開啟。

```powershell
$gpu = 'pod-YOUR-ID@ssh.gputw.ai'
scp -P 2222 .\scripts\setup_gputw.sh "${gpu}:/vault/setup_gputw.sh"
ssh -p 2222 -o ExitOnForwardFailure=yes -L 127.0.0.1:18188:127.0.0.1:8188 $gpu "bash /vault/setup_gputw.sh"
```

腳本在雲端安裝 ComfyUI、KJNodes、SelfLift、VideoHelperSuite、LayerStyle、SageAttention 與 FFmpeg，並將下列五個權重直接下載到 `/vault/story-h3/models`。兩個 workflow 共用同一份模型。第一次需等候編譯和下載，看到 ComfyUI 開始監聽後，在 Windows 開啟 [連線檢查](http://127.0.0.1:18188/system_stats)，看到含有 `devices` 的 JSON 即表示通道可用。[安裝腳本與檔名](../scripts/setup_gputw.sh)

[INT8 diffusion model](https://huggingface.co/OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM/tree/main) · [NVFP4 encoder](https://huggingface.co/czb2010/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4/tree/main) · [Video and audio VAEs](https://huggingface.co/Comfy-Org/MiniMax-H3/tree/main/vae) · [3D latent upscaler](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler/blob/27236837051793bc7cccf628e5936c6110361dab/minimax_h3_latent_upscaler_3d_fp16.safetensors)

## 5. 在本機產出第一段影片

1. 專案頁的 **連線設定** 填入 `http://127.0.0.1:18188`，取消 **與 ComfyUI 共用 GPU**。按 **儲存 → 測試連線**，應顯示 **ComfyUI 已連線**。這個本機網址會經 SSH 通往雲端。
2. 在分鏡卡依下表選擇 **Workflow template** 與 **H3 任務**，按 **套用**。舊專案也要重新套用才能更新片段的範本快照。

| 想做的影片 | Workflow template | H3 任務與素材 |
| --- | --- | --- |
| 只用文字，適合第一次試跑 | `10eros_singularity_t2va_template` | `T2VA`，不用上傳素材 |
| 用參考外觀和聲音 | `10eros_singularity_ref2va_template` | `Ref2VA`，上傳一張圖片與一段音訊，在 REF 插槽指定 `REF_IMG0`、`REF_AUD0` 並按 **儲存媒體順序**。內建角色也可同時提供兩者。 |

3. 選短片段及較低解析度。按 **生成新 prompt**，檢查後按 **生成/重製本段影片**。分鏡卡出現可播放的 MP4 即完成首次試跑。片段產生後可 **設為 active**，最後 **合併 active takes**。

## 常見卡點與收工

- **找不到模型或節點**：看雲端終端機最早的錯誤。確認下載及依賴安裝完成；`nvcc` 或 SageAttention 出錯時，檢查所選映像是否提供與 PyTorch 相容的 CUDA 開發工具。結束服務後重跑步驟 4，已完整下載的模型會沿用。
- **連線被拒／出現登入頁**：確認實例仍為 RUNNING、SSH 視窗仍開啟，且使用步驟 5 的本機網址。若 18188 已被占用，可將指令左側的 18188 及工作台網址一起改為 18189。
- **顯存不足**：先縮短片段或降低解析度再試；若仍不足，改租較大 VRAM 的 GPU。
- **完成後**：先確認本機可播放影片，再到 **Instances → Stop**，確認狀態為停止。單純關閉網頁或 SSH 視窗，GPU 仍可能計費。模型與輸出保存在 Vault，儲存費另計；下次啟動相同開發環境後，重跑步驟 4 即可重建環境。[Vault 持久化與計費](https://docs.gputw.ai/en/docs/vault)
