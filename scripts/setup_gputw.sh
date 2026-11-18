#!/usr/bin/env bash
# Run inside the GPUtw PyTorch/CUDA development instance.
set -euo pipefail
test -d /vault || { echo "Mount the GPUtw Vault first."; exit 1; }
cd /workspace
command -v nvcc >/dev/null || { echo "Choose a CUDA development image with nvcc."; exit 1; }
python3 -c 'import torch; assert torch.cuda.is_available(), "CUDA PyTorch required"; print(torch.cuda.get_device_name())'
if [[ $(id -u) == 0 ]]; then elevate=(); else elevate=(sudo); fi
"${elevate[@]}" apt-get update
"${elevate[@]}" apt-get install -y git curl ffmpeg python3-venv build-essential ninja-build libgl1 libglib2.0-0
mkdir -p story-h3
cd story-h3
if [[ ! -d ComfyUI ]]; then git clone --depth 1 --branch v0.38.0 https://github.com/Comfy-Org/ComfyUI.git; fi
cd ComfyUI
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
for repo in kijai/ComfyUI-KJNodes Kosinkadink/ComfyUI-VideoHelperSuite chflame163/ComfyUI_LayerStyle facok/comfyui-SelfLift; do
    dest="custom_nodes/${repo##*/}"
    if [[ ! -d "$dest" ]]; then git clone "https://github.com/$repo.git" "$dest"; fi
    if [[ "$repo" == facok/comfyui-SelfLift ]]; then git -C "$dest" checkout 835c3cf7919410f71f414688f049c6ca497789d0; fi
    if [[ -f "$dest/requirements.txt" ]]; then python -m pip install -r "$dest/requirements.txt"; fi
done
python -m pip install wheel setuptools packaging
MAX_JOBS=4 python -m pip install --no-build-isolation sageattention==2.2.0
python -c 'from sageattention.core import get_cuda_arch_versions; print(get_cuda_arch_versions())'

download() {
    local dir="/vault/story-h3/models/$1" file="$2" source="$3"
    mkdir -p "$dir"
    if [[ ! -s "$dir/$file" ]]; then
        curl --fail --location --retry 4 --continue-at - --output "$dir/$file.part" "https://huggingface.co/$source/$file"
        mv "$dir/$file.part" "$dir/$file"
    fi
    mkdir -p "models/$1"
    ln -sfn "$dir/$file" "models/$1/$file"
}
download diffusion_models Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8.safetensors OliviaRossi/Minimax-h3_Singularity-x-10Eros-Max_PRISM/resolve/main
download text_encoders qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors czb2010/Qwen3-VL-32B-Heretic-MiniMax-H3-NVFP4/resolve/main
download vae minimax_h3_video_vae_fp16.safetensors Comfy-Org/MiniMax-H3/resolve/main/vae
download vae minimax_h3_audio_vae_fp32.safetensors Comfy-Org/MiniMax-H3/resolve/main/vae
download latent_upscale_models minimax_h3_latent_upscaler_3d_fp16.safetensors LBH-123-AI/Minimax_h3_latent_Upscaler/resolve/27236837051793bc7cccf628e5936c6110361dab
exec python main.py --listen 127.0.0.1 --port 8188 --output-directory /vault/story-h3/output
