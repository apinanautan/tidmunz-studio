"""Remove one image background with SnapGen's isolated RMBG 2.0 install."""
from __future__ import annotations

import argparse
import gc
import warnings
from pathlib import Path


def progress(value: int, text: str) -> None:
    print(f"PROGRESS|{value}|{text}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    args = parser.parse_args()
    if not args.input.is_file():
        raise FileNotFoundError(f"ไม่พบรูปต้นฉบับ: {args.input}")
    if not (args.model_dir / "model.safetensors").is_file():
        raise FileNotFoundError(f"ไม่พบ RMBG 2.0: {args.model_dir}")

    warnings.filterwarnings("ignore", category=FutureWarning, module=r"timm\..*")
    import torch
    from PIL import Image
    from torchvision import transforms
    from transformers import AutoModelForImageSegmentation
    from transformers.utils import logging as transformers_logging

    transformers_logging.set_verbosity_error()
    transformers_logging.disable_progress_bar()
    progress(2, "กำลังโหลด RMBG 2.0")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForImageSegmentation.from_pretrained(
        str(args.model_dir), trust_remote_code=True, local_files_only=True,
    ).to(device).eval()
    image = Image.open(args.input).convert("RGB")
    tensor = transforms.Compose([
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])(image).unsqueeze(0).to(device)
    progress(6, "กำลังลบพื้นหลัง")
    with torch.no_grad():
        mask = model(tensor)[-1].sigmoid().float().cpu()[0].squeeze()
    alpha = transforms.ToPILImage()(mask).resize(image.size, Image.Resampling.LANCZOS)
    foreground = image.convert("RGBA")
    foreground.putalpha(alpha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    foreground.save(args.output)
    del tensor, mask, model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    progress(10, "ลบพื้นหลังเสร็จ")
    print(f"RESULT|{args.output.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR|{exc}", flush=True)
        raise SystemExit(1)
