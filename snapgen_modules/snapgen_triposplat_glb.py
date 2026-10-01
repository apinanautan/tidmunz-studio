# -*- coding: utf-8 -*-
"""Isolated TripoSplat worker for SnapGen Prop.

Heavy dependencies and model files belong in snapgen_data/tools/triposplat.
This process must not import or modify SnapGen's main Python environment.
"""
from __future__ import annotations

import argparse
import gc
import shutil
import subprocess
import sys
import warnings
from pathlib import Path


_LAST_PROGRESS = None


def _progress(percent: int, text: str) -> None:
    global _LAST_PROGRESS
    value = max(0, min(100, int(percent)))
    if value == _LAST_PROGRESS:
        return
    _LAST_PROGRESS = value
    print(f"PROGRESS|{value}|{text}", flush=True)


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"ไม่พบ {label}: {path}")
    return path


def _find_blender() -> Path | None:
    found = shutil.which("blender")
    if found:
        return Path(found)
    base = Path(r"C:\Program Files\Blender Foundation")
    candidates = sorted(base.glob("Blender */blender.exe"), reverse=True) if base.is_dir() else []
    return candidates[0] if candidates else None


def _remove_background(image_path: Path, output_path: Path, model_dir: Path) -> Path:
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"timm\..*")
    import torch
    from PIL import Image
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation
    from transformers.utils import logging as transformers_logging

    transformers_logging.set_verbosity_error()
    transformers_logging.disable_progress_bar()

    _progress(3, "กำลังโหลด RMBG 2.0")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForImageSegmentation.from_pretrained(
        str(model_dir),
        trust_remote_code=True,
        local_files_only=True,
    ).to(device).eval()
    image = Image.open(image_path).convert("RGB")
    tensor = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])(image).unsqueeze(0).to(device)
    _progress(7, "RMBG 2.0 กำลังลบพื้นหลัง")
    with torch.no_grad():
        mask = model(tensor)[-1].sigmoid().float().cpu()[0].squeeze()
    alpha = transforms.ToPILImage()(mask).resize(image.size, Image.Resampling.LANCZOS)
    foreground = image.convert("RGBA")
    foreground.putalpha(alpha)
    foreground.save(output_path)
    del tensor, mask, model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    _progress(15, "RMBG 2.0 ลบพื้นหลังเสร็จ")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mesh-resolution", type=int, default=512, choices=(512, 768, 1024))
    parser.add_argument("--asset-name", default="")
    args = parser.parse_args()

    tool_dir = Path.cwd()
    source_dir = tool_dir / "source"
    ckpts = tool_dir / "ckpts"
    bg_model_dir = tool_dir / "bg_models" / "RMBG-2.0"
    image_path = _require_file(Path(args.input), "รูปต้นฉบับ")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_name = args.asset_name.strip() or "prop"

    _require_file(source_dir / "triposplat.py", "source/triposplat.py")
    _require_file(source_dir / "model.py", "source/model.py")
    _require_file(bg_model_dir / "model.safetensors", "RMBG 2.0")
    sys.path.insert(0, str(source_dir))

    foreground_path = _remove_background(
        image_path,
        output_dir / "background_removed.png",
        bg_model_dir,
    )

    try:
        from triposplat import TripoSplatPipeline
    except Exception as exc:
        raise RuntimeError(f"โหลด TripoSplat ไม่สำเร็จ: {exc}") from exc

    _progress(17, "กำลังโหลดโมเดล 3D")
    pipe = TripoSplatPipeline(
        ckpt_path=str(_require_file(ckpts / "diffusion_models" / "triposplat_fp16.safetensors", "โมเดลหลัก")),
        decoder_path=str(_require_file(ckpts / "vae" / "triposplat_vae_decoder_fp16.safetensors", "ตัวถอด Gaussian")),
        dinov3_path=str(_require_file(ckpts / "clip_vision" / "dino_v3_vit_h.safetensors", "DINOv3")),
        flux2_vae_encoder_path=str(_require_file(ckpts / "vae" / "flux2-vae.safetensors", "Flux VAE")),
        rmbg_path=str(_require_file(ckpts / "background_removal" / "birefnet.safetensors", "ตัวลบพื้นหลัง")),
        device="cuda",
    )
    _progress(25, "โหลดโมเดล 3D เสร็จ")
    gaussian, prepared = pipe.run(
        str(foreground_path),
        seed=46,
        num_gaussians=262144,
        erode_radius=1,
        show_progress=False,
        callback=lambda step, total: _progress(25 + (step / total) * 45, f"สร้าง Gaussian {step}/{total}"),
    )
    _progress(72, "กำลังบันทึก Splat")
    prepared.save(output_dir / "prepared.webp")
    gaussian.save_ply(output_dir / "source.ply")
    gaussian.save_splat(output_dir / "source.splat")

    # ponytail: direct mesh extraction is intentionally delegated to a tiny
    # optional module copied from ComfyUI's SplatToMesh algorithm. Replace this
    # boundary only when upstream publishes a standalone converter.
    try:
        from snapgen_splat_to_glb import save_gaussian_as_glb
    except Exception as exc:
        raise RuntimeError(
            "ยังไม่มีตัวแปลง Splat → GLB ใน environment TripoSplat "
            "(ต้องมี source/snapgen_splat_to_glb.py)"
        ) from exc

    _progress(75, f"เริ่มสร้าง Mesh ความละเอียด {args.mesh_resolution}")
    save_gaussian_as_glb(
        gaussian,
        output_dir / f"{asset_name}.glb",
        resolution=args.mesh_resolution,
        progress=lambda value: _progress(75 + value * 19, "กำลังสร้าง Mesh/GLB"),
    )
    _progress(95, "สร้าง GLB เสร็จ — กำลังแปลง FBX")
    blender = _find_blender()
    blender_script = Path(__file__).resolve().parent / "snapgen_blender_glb_to_fbx.py"
    if blender and blender_script.is_file():
        result = subprocess.run(
            [
                str(blender), "--background", "--python", str(blender_script), "--",
                str(output_dir / f"{asset_name}.glb"), str(output_dir / f"{asset_name}.fbx"), asset_name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not (output_dir / f"{asset_name}.fbx").is_file():
            print("WARNING: แปลง FBX ไม่สำเร็จ — ยังเก็บ GLB ไว้", flush=True)
            if result.stderr.strip():
                print(result.stderr.strip().splitlines()[-1], flush=True)
        else:
            print("สร้าง FBX สำเร็จ", flush=True)
    else:
        print("WARNING: ไม่พบ Blender — ข้าม FBX และเก็บ GLB", flush=True)
    _progress(100, "เสร็จ: GLB + FBX")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(1)
