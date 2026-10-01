#!/bin/bash
# vast_setup.sh — รันบน Vast.ai instance ใหม่หลัง SSH เข้า
# ดาวน์โหลดโมเดล MiniMax H3 เข้า volume และ config ComfyUI
set -e

VOLUME_PATH="/workspace"   # Vast.ai mount volume ที่ /workspace โดย default
MODEL_DIR="$VOLUME_PATH/ComfyUI/models"
COMFY_DIR="/workspace/ComfyUI"
HF_TOKEN=""  # ใส่ HuggingFace token ถ้าโมเดล private

echo "=== SnapGen MiniMax H3 Setup ==="
echo "Volume: $VOLUME_PATH"
echo "Model dir: $MODEL_DIR"

# สร้าง folder structure
mkdir -p "$MODEL_DIR/unet"
mkdir -p "$MODEL_DIR/clip"
mkdir -p "$MODEL_DIR/vae"
mkdir -p "$MODEL_DIR/loras"

# ดาวน์โหลดโมเดล MiniMax H3
echo ""
echo "--- Downloading MiniMax H3 models ---"

# UNET (fp8 pruned ~20GB)
if [ ! -f "$MODEL_DIR/unet/minimax_h3_fl2va_pruned_fp8_scaled.safetensors" ]; then
    echo "Downloading UNET (pruned fp8)..."
    wget -q --show-progress -O "$MODEL_DIR/unet/minimax_h3_fl2va_pruned_fp8_scaled.safetensors" \
        "https://huggingface.co/Comfy-Org/MiniMax-H3-Turbo/resolve/main/split_files/diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors"
else
    echo "UNET pruned already exists, skip"
fi

# CLIP / Text Encoder (~15GB)
if [ ! -f "$MODEL_DIR/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" ]; then
    echo "Downloading CLIP (Qwen3 32B)..."
    wget -q --show-progress -O "$MODEL_DIR/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" \
        "https://huggingface.co/Comfy-Org/MiniMax-H3-Turbo/resolve/main/split_files/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
else
    echo "CLIP already exists, skip"
fi

# VAE Video
if [ ! -f "$MODEL_DIR/vae/minimax_h3_video_vae_fp16.safetensors" ]; then
    echo "Downloading VAE Video..."
    wget -q --show-progress -O "$MODEL_DIR/vae/minimax_h3_video_vae_fp16.safetensors" \
        "https://huggingface.co/Comfy-Org/MiniMax-H3-Turbo/resolve/main/split_files/vae/minimax_h3_video_vae_fp16.safetensors"
else
    echo "VAE Video already exists, skip"
fi

# VAE Audio
if [ ! -f "$MODEL_DIR/vae/minimax_h3_audio_vae_fp32.safetensors" ]; then
    echo "Downloading VAE Audio..."
    wget -q --show-progress -O "$MODEL_DIR/vae/minimax_h3_audio_vae_fp32.safetensors" \
        "https://huggingface.co/Comfy-Org/MiniMax-H3-Turbo/resolve/main/split_files/vae/minimax_h3_audio_vae_fp32.safetensors"
else
    echo "VAE Audio already exists, skip"
fi

# Turbo LoRA — ข้าม (คุณภาพต่ำ ใช้ full model แทน)

echo ""
echo "--- Configuring ComfyUI extra_model_paths ---"
cat > "$COMFY_DIR/extra_model_paths.yaml" << EOF
snapgen_volume:
    base_path: $MODEL_DIR
    unet: unet
    clip: clip
    vae: vae
    loras: loras
EOF

echo "Config written to $COMFY_DIR/extra_model_paths.yaml"

echo ""
echo "--- Disk usage ---"
du -sh "$MODEL_DIR"/* 2>/dev/null || true
df -h "$VOLUME_PATH"

echo ""
echo "=== Done! Restart ComfyUI to apply ==="
echo "killall python && cd $COMFY_DIR && python main.py --port 18188 --enable-cors-header &"
