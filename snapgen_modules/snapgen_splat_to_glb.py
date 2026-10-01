"""Use ComfyUI core's SplatToMesh algorithm without starting ComfyUI."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import torch


def save_gaussian_as_glb(gaussian, output_path, resolution=256, progress=None):
    tool_dir = Path(__file__).resolve().parent.parent
    comfy_dir = tool_dir / "comfy_source"
    if not comfy_dir.is_dir():
        raise FileNotFoundError(f"ไม่พบ ComfyUI core: {comfy_dir}")
    if "server" not in sys.modules:
        server_stub = types.ModuleType("server")
        server_stub.PromptServer = type("PromptServer", (), {"instance": None})
        sys.modules["server"] = server_stub
    sys.path.insert(0, str(comfy_dir))
    from comfy_api.latest import Types
    from comfy_extras.nodes_gaussian_splat import _gaussian_to_mesh
    from comfy_extras.nodes_save_3d import save_glb

    rotations = gaussian._rotation + gaussian.rots_bias[None, :]
    rotations = rotations / rotations.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    splat = Types.SPLAT(
        positions=gaussian.get_xyz[None], scales=gaussian.get_scaling[None],
        rotations=rotations[None], opacities=gaussian.get_opacity[None],
        sh=gaussian._features_dc[None],
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    result = _gaussian_to_mesh(splat, 0, int(resolution), 5, 0, 0.4, 500, 0.02, 2.0, device, progress)
    if result is None:
        raise RuntimeError("Splat ไม่เกิดพื้นผิว Mesh")
    vertices, faces, colors = result
    save_glb(
        vertices, faces, str(output_path), vertex_colors=colors, unlit=True,
        metadata={"generator": "SnapGen TripoSplat + ComfyUI SplatToMesh"},
    )
