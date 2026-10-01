"""รัน script นี้บนเครื่องตัวเอง — SSH เข้า instance แล้วโหลดโมเดล MiniMax H3 อัตโนมัติ"""
import json, subprocess, sys, time
from pathlib import Path

# โหลด config
PROJECT_ROOT = Path(__file__).resolve().parents[2]
cfg = json.loads((PROJECT_ROOT / "snapgen_data" / "minimax_cloud.json").read_text(encoding="utf-8"))
SSH_KEY  = cfg.get("ssh_key") or r"C:\Users\Apinan\.ssh\id_ed25519"
SSH_HOST = cfg.get("ssh_host") or "ssh5.vast.ai"
SSH_PORT = int(cfg.get("ssh_port") or 36034)
SSH_USER = cfg.get("ssh_user") or "root"

_HF_BASE = "https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot/resolve/main"
_HF_BASE_CLIP = "https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot/resolve/main/text_encoders"
_HF_BASE_VAE  = "https://huggingface.co/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot/resolve/main/vae"

def ssh(cmd, timeout=3600):
    result = subprocess.run([
        r"C:\Windows\System32\OpenSSH\ssh.exe",
        "-i", SSH_KEY,
        "-p", str(SSH_PORT),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=20",
        f"{SSH_USER}@{SSH_HOST}",
        cmd
    ], capture_output=True, text=True, timeout=timeout)
    print(result.stdout, end="")
    if result.stderr: print(result.stderr, end="")
    return result.returncode

print(f"=== SSH → {SSH_USER}@{SSH_HOST}:{SSH_PORT} ===")

# ทดสอบ connection
print("ทดสอบ SSH...")
rc = ssh("echo 'SSH OK' && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader", timeout=30)
if rc != 0:
    print("ERROR: SSH ไม่ได้ — ตรวจสอบ key และ instance")
    sys.exit(1)

# ดู disk
print("\nดู disk...")
ssh("df -h /workspace")

# โหลดโมเดล — ใช้ Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot (public, ไม่ต้อง token)
MODELS = [
    # (ชื่อไฟล์แสดง, URL, path บน server)
    (
        "UNET int8 convrot (~20GB)",
        f"{_HF_BASE}/MiniMax_H3_FL2VA_pruned_int8_convrot.safetensors",
        "/workspace/ComfyUI/models/unet/minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    ),
    (
        "CLIP Qwen3 32B nvfp4 (~15GB)",
        f"{_HF_BASE_CLIP}/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "/workspace/ComfyUI/models/clip/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    ),
    (
        "VAE Video fp16",
        f"{_HF_BASE_VAE}/minimax_h3_video_vae_fp16.safetensors",
        "/workspace/ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors",
    ),
    (
        "VAE Audio fp32",
        f"{_HF_BASE_VAE}/minimax_h3_audio_vae_fp32.safetensors",
        "/workspace/ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors",
    ),
]

print("\n=== เริ่มโหลดโมเดล ===")
for name, url, dest in MODELS:
    print(f"\n--- {name} ---")
    check = subprocess.run([
        r"C:\Windows\System32\OpenSSH\ssh.exe",
        "-i", SSH_KEY, "-p", str(SSH_PORT),
        "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=20",
        f"{SSH_USER}@{SSH_HOST}",
        f"test -f '{dest}' && echo EXISTS || echo MISSING"
    ], capture_output=True, text=True, timeout=30)
    if "EXISTS" in check.stdout:
        print(f"  มีอยู่แล้ว — ข้าม")
        continue
    print(f"  โหลด {url}")
    dl = (f"echo 'nameserver 8.8.8.8' > /etc/resolv.conf && "
          f"mkdir -p $(dirname '{dest}') && "
          f"if command -v aria2c &>/dev/null; then aria2c -x4 -s4 --dir=$(dirname '{dest}') --out=$(basename '{dest}') '{url}'; "
          f"elif command -v wget &>/dev/null; then wget -q --show-progress -O '{dest}' '{url}'; "
          f"else curl -L --retry 3 -o '{dest}' '{url}'; fi")
    rc = ssh(dl, timeout=3600)
    if rc != 0:
        print(f"  ERROR: โหลด {name} ไม่สำเร็จ")
        sys.exit(1)
    print(f"  เสร็จ!")

print("\n=== ดู disk หลังโหลด ===")
ssh("df -h /workspace && du -sh /workspace/ComfyUI/models/*/")

print("\n=== เสร็จทั้งหมด! ===")
print("รีสตาร์ท ComfyUI เพื่อให้โหลดโมเดลใหม่...")
ssh("pkill -f 'python.*main.py' || true; sleep 2; cd /workspace/ComfyUI && nohup python main.py --port 18188 --enable-cors-header > /tmp/comfy.log 2>&1 &")
print("ComfyUI กำลัง restart — รอ 30 วินาที...")
time.sleep(30)
print("เสร็จ! พร้อมใช้งาน")
