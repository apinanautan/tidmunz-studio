"""Generate practical PBR maps with StableNormal-turbo in an isolated tool env."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def _periodic_canvas(image: Image.Image) -> Image.Image:
    """Give AI neighboring copies so opposite texture edges share context."""
    tile = image.convert("RGB").resize((384, 384), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (1152, 1152))
    for y in range(3):
        for x in range(3):
            canvas.paste(tile, (x * 384, y * 384))
    return canvas


def _stable_normal(source: Path, output: Path, tool_dir: Path) -> None:
    import torch
    from huggingface_hub import snapshot_download

    repo = tool_dir / "source"
    os.environ.setdefault("HF_HOME", str(tool_dir / "hf_cache"))
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        raise RuntimeError("StableNormal ต้องใช้ NVIDIA GPU")
    print("PROGRESS|15|กำลังโหลด StableNormal-turbo", flush=True)
    weights = tool_dir / "weights"
    model_dir = weights / "yoso-normal-v0-3"
    if not (model_dir / "model_index.json").is_file():
        print("PROGRESS|20|กำลังดาวน์โหลดโมเดลครั้งแรก", flush=True)
        snapshot_download(
            repo_id="Stable-X/yoso-normal-v0-3",
            local_dir=str(model_dir),
        )
    predictor = torch.hub.load(
        str(repo), "StableNormal_turbo", source="local", trust_repo=True,
        device=device, local_cache_dir=str(weights),
    )
    print("PROGRESS|55|กำลังวิเคราะห์รูปทรงผิวด้วย AI", flush=True)
    tiled = _periodic_canvas(Image.open(source))
    predicted = predictor(tiled).convert("RGB")
    # Crop center copy. Neighboring copies reduce false seams at tile borders.
    w, h = predicted.size
    normal = predicted.crop((w // 3, h // 3, 2 * w // 3, 2 * h // 3))
    normal = normal.resize(Image.open(source).size, Image.Resampling.LANCZOS)
    normal_array = np.asarray(normal, dtype=np.float32).copy()
    # Exact periodic boundaries prevent a one-pixel seam after tiling/mipmaps.
    vertical = (normal_array[:, 0] + normal_array[:, -1]) * 0.5
    horizontal = (normal_array[0] + normal_array[-1]) * 0.5
    normal_array[:, 0] = normal_array[:, -1] = vertical
    normal_array[0] = normal_array[-1] = horizontal
    Image.fromarray(normal_array.clip(0, 255).astype(np.uint8), "RGB").save(output)


def _roughness(source: Path, output: Path, kind: str) -> None:
    image = Image.open(source).convert("L")
    gray = np.asarray(image, dtype=np.float32) / 255.0
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(5)), dtype=np.float32) / 255.0
    micro = np.abs(gray - blurred)
    gx = np.roll(gray, -1, axis=1) - np.roll(gray, 1, axis=1)
    gy = np.roll(gray, -1, axis=0) - np.roll(gray, 1, axis=0)
    detail = np.clip(micro * 3.2 + np.sqrt(gx * gx + gy * gy) * 1.4, 0.0, 1.0)
    defaults = {
        "ผ้า": 0.82, "ไม้": 0.64, "โลหะ": 0.34, "หิน": 0.80,
        "หนัง": 0.57, "พลาสติก": 0.46, "ธรรมชาติ": 0.74, "กำหนดเอง": 0.63,
    }
    base = defaults.get(kind, 0.63)
    rough = np.clip(base + (detail - detail.mean()) * 0.34, 0.08, 0.96)
    Image.fromarray((rough * 255).astype(np.uint8), "L").save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--kind", default="กำหนดเอง")
    parser.add_argument("--tool-dir", required=True)
    args = parser.parse_args()
    source = Path(args.input).resolve()
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = Image.open(source).convert("RGB")
    base.save(out / "BaseColor.png")
    _stable_normal(source, out / "Normal.png", Path(args.tool_dir).resolve())
    print("PROGRESS|85|กำลังสร้าง Roughness ตามรายละเอียดผิว", flush=True)
    _roughness(source, out / "Roughness.png", args.kind)
    # Metallic is a physical material property. Fabric/wood/stone must remain 0.
    metallic = 255 if args.kind == "โลหะ" else 0
    Image.new("L", base.size, metallic).save(out / "Metallic.png")
    print(f"RESULT|{out / 'BaseColor.png'}", flush=True)
    print(f"RESULT|{out / 'Normal.png'}", flush=True)
    print(f"RESULT|{out / 'Metallic.png'}", flush=True)
    print(f"RESULT|{out / 'Roughness.png'}", flush=True)
    print("PROGRESS|100|สร้าง PBR สำเร็จ", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
