"""Run TRELLIS.2 directly from Python without ComfyUI or a local HTTP API."""
from __future__ import annotations

import argparse
import os
os.environ.setdefault("SETUPTOOLS_USE_DISTUTILS", "stdlib")
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

import shutil
import subprocess
import sys
from pathlib import Path


def progress(value: int, text: str) -> None:
    print(f"PROGRESS|{value}|{text}", flush=True)


def find_blender() -> Path | None:
    found = shutil.which("blender")
    if found:
        return Path(found)
    candidate = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")
    return candidate if candidate.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tool-dir", required=True, type=Path)
    parser.add_argument("--asset-name", default="")
    parser.add_argument("--resolution", type=int, choices=(512, 768, 1024), default=1024)
    args = parser.parse_args()

    image_path = args.input.resolve()
    output_dir = args.output.resolve()
    tool_dir = args.tool_dir.resolve()
    code_dir = tool_dir / "code"
    asset_name = args.asset_name.strip() or "prop"
    if not image_path.is_file():
        raise RuntimeError(f"ไม่พบรูป: {image_path}")
    if not (code_dir / "pipeline_worker.py").is_file():
        raise RuntimeError(f"TRELLIS.2 ยังติดตั้งไม่ครบ: {tool_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(code_dir / "models")
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "garbage_collection_threshold:0.65"
    sys.path.insert(0, str(code_dir))

    from PIL import Image
    from pipeline_worker import PipelineWorker

    pipeline_type = "512" if args.resolution <= 768 else "1024_cascade"
    ss_params = {
        "steps": 14,
        "guidance_strength": 7.5,
        "guidance_rescale": 0.7,
        "rescale_t": 5.0,
    }
    shape_params = {
        "steps": 14,
        "guidance_strength": 7.5,
        "guidance_rescale": 0.5,
        "rescale_t": 3.0,
    }
    tex_params = {
        "steps": 14,
        "guidance_strength": 1.0,
        "guidance_rescale": 0.0,
        "rescale_t": 3.0,
    }
    profiling = {
        "enable_python": False,
        "enable_torch": False,
        "enable_sync_hunter": False,
        "delay_sec": 0,
        "max_duration_sec": 0,
        "max_events": 0,
    }

    worker = None
    try:
        progress(5, "กำลังโหลดโมเดล TRELLIS.2 โดยตรง")
        worker = PipelineWorker()
        progress(12, "กำลังเตรียมรูป")
        image = worker.preprocess(Image.open(image_path).convert("RGBA"))
        heartbeat = [12]

        def on_wait() -> None:
            heartbeat[0] = min(72, heartbeat[0] + 2)
            progress(heartbeat[0], "กำลังสร้าง Shape และ PBR Texture")

        state, _preview = worker.generate(
            image=image,
            seed=1234,
            ss_params=ss_params,
            shape_params=shape_params,
            tex_params=tex_params,
            pipeline_type=pipeline_type,
            nviews=6,
            profiling=profiling,
            progress_callback=on_wait,
        )
        glb_path = output_dir / f"{asset_name}.glb"
        progress(80, "กำลังสร้าง Mesh และอบ Texture 4K")
        worker.extract_glb(
            state=state,
            # ponytail: TRELLIS.2 exporter requires a target; use its maximum so
            # SnapGen does not reduce the mesh to the old 300k preview setting.
            decimation_target=1000000,
            texture_size=4096,
            glb_path=str(glb_path),
        )
        if not glb_path.is_file():
            raise RuntimeError("TRELLIS.2 ไม่ได้สร้าง GLB")

        blender = find_blender()
        converter = Path(__file__).resolve().parent / "snapgen_blender_glb_to_fbx.py"
        if blender and converter.is_file():
            progress(94, "กำลังแปลง FBX สำหรับ iClone")
            fbx_path = output_dir / f"{asset_name}.fbx"
            result = subprocess.run(
                [str(blender), "--background", "--python", str(converter), "--",
                 str(glb_path), str(fbx_path), asset_name],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0 and fbx_path.is_file():
                print(f"RESULT|{fbx_path}", flush=True)
            elif result.returncode != 0:
                print("WARNING: แปลง FBX ไม่สำเร็จ — ยังเก็บ GLB ไว้", flush=True)

        progress(100, "เสร็จแล้ว")
        print(f"RESULT|{glb_path}", flush=True)
        return 0
    finally:
        if worker is not None:
            worker.shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(1)
