"""Blender background script: convert SnapGen GLB to FBX with vertex colors."""
from __future__ import annotations

import sys
from pathlib import Path

import bpy
import numpy as np


def _save_rgb(image, path: Path) -> None:
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "RGB"
    image.save_render(str(path))


def _save_channel(image, channel: int, path: Path) -> bpy.types.Image:
    image.reload()
    width, height = image.size
    pixels = np.asarray(image.pixels[:], dtype=np.float32).reshape(-1, 4)
    if len(pixels) != width * height:
        raise RuntimeError(f"อ่าน Pixel ของ {image.name} ไม่ครบ")
    value = pixels[:, channel]
    rgba = np.column_stack((value, value, value, np.ones_like(value))).ravel()
    output = bpy.data.images.new(path.stem, width=width, height=height, alpha=False)
    output.pixels.foreach_set(rgba)
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "BW"
    output.save_render(str(path))
    return bpy.data.images.load(str(path), check_existing=False)


def _save_constant(value: float, path: Path) -> bpy.types.Image:
    image = bpy.data.images.new(path.stem, width=16, height=16, alpha=False)
    rgba = np.tile(np.array([value, value, value, 1.0], dtype=np.float32), 16 * 16)
    image.pixels.foreach_set(rgba)
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "BW"
    image.save_render(str(path))
    return bpy.data.images.load(str(path), check_existing=False)


def _save_normal(base_color: Path, path: Path, strength: float = 2.0) -> bpy.types.Image:
    image = bpy.data.images.load(str(base_color), check_existing=False)
    image.reload()
    width, height = image.size
    rgba = np.asarray(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
    luminance = rgba[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    dx = np.roll(luminance, -1, axis=1) - np.roll(luminance, 1, axis=1)
    dy = np.roll(luminance, -1, axis=0) - np.roll(luminance, 1, axis=0)
    normal = np.dstack((-dx * strength, -dy * strength, np.ones_like(luminance)))
    normal /= np.linalg.norm(normal, axis=2, keepdims=True).clip(min=1e-6)
    normal = normal * 0.5 + 0.5
    output_rgba = np.dstack((normal, np.ones_like(luminance))).ravel()
    output = bpy.data.images.new(path.stem, width=width, height=height, alpha=False)
    output.pixels.foreach_set(output_rgba)
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.context.scene.render.image_settings.color_mode = "RGB"
    output.save_render(str(path))
    return bpy.data.images.load(str(path), check_existing=False)


def _bake_vertex_color(obj, texture_dir: Path) -> None:
    if not obj.data.color_attributes:
        return
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    while obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers[0])
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15192, island_margin=0.0005)
    bpy.ops.object.mode_set(mode="OBJECT")
    material = bpy.data.materials.new("Material")
    material.use_nodes = True
    obj.data.materials.clear()
    obj.data.materials.append(material)
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    color = nodes.new("ShaderNodeVertexColor")
    color.layer_name = obj.data.color_attributes.active_color.name
    target = nodes.new("ShaderNodeTexImage")
    target.image = bpy.data.images.new("BaseColor", width=4096, height=4096, alpha=False)
    target.image["snapgen_baked_vertex_color"] = True
    nodes.active = target
    target.select = True
    links.new(color.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "CPU"
    bpy.context.scene.render.bake.margin = 4
    bpy.ops.object.bake(type="EMIT")
    texture_path = texture_dir / "BaseColor.png"
    _save_rgb(target.image, texture_path)
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(texture_path), check_existing=False)
    texture.image["snapgen_baked_vertex_color"] = True
    links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    print(f"VERTEX_COLOR_BAKED|{texture_path}", flush=True)


def main() -> None:
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) not in (2, 3):
        raise RuntimeError("ต้องระบุ source.glb, output.fbx และ asset name (ถ้ามี)")
    source, output = map(Path, args[:2])
    asset_name = args[2].strip() if len(args) == 3 else ""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if source.suffix.lower() == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(source))
    else:
        bpy.ops.import_scene.gltf(filepath=str(source))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("GLB ไม่มี Mesh")
    if asset_name:
        for index, obj in enumerate(meshes, 1):
            name = asset_name if len(meshes) == 1 else f"{asset_name}_{index}"
            obj.name = name
            obj.data.name = f"{name}_Mesh"
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if asset_name and source.suffix.lower() == ".glb":
        named_glb = source.with_name(f".{source.stem}.named.glb")
        bpy.ops.export_scene.gltf(
            filepath=str(named_glb), export_format="GLB", use_selection=True,
        )
        named_glb.replace(source)
        print(f"GLB_NAME_OK|{asset_name}|{source}", flush=True)
    for obj in meshes:
        original_faces = len(obj.data.polygons)
        bpy.context.view_layer.objects.active = obj
        vertex_color_only = bool(obj.data.color_attributes) and not bpy.data.images
        original = None
        # TripoSplat FBX stays light for iClone; textured TRELLIS.2 keeps its
        # existing ceiling and bypasses this TripoSplat-only remesh branch.
        max_faces = 100_000 if vertex_color_only else 850_000
        if vertex_color_only:
            # TripoSplat: preserve the untouched generated mesh as color source,
            # remesh the full geometry in Blender, then transfer color back.
            original = obj.copy()
            original.data = obj.data.copy()
            bpy.context.collection.objects.link(original)
            dimensions = max(obj.dimensions)
            remesh = obj.modifiers.new(name="SnapGen Blender Remesh", type="REMESH")
            remesh.mode = "VOXEL"
            remesh.voxel_size = dimensions / 360
            remesh.use_smooth_shade = True
            bpy.ops.object.modifier_apply(modifier=remesh.name)
            print(f"REMESH_OK|{original_faces}|{len(obj.data.polygons)}", flush=True)
        for pass_number in range(1, 4):
            face_count = len(obj.data.polygons)
            if face_count <= max_faces:
                break
            modifier = obj.modifiers.new(name=f"SnapGen final {max_faces // 1000}K pass {pass_number}", type="DECIMATE")
            modifier.decimate_type = "COLLAPSE"
            modifier.ratio = min(1.0, max_faces / face_count)
            modifier.use_collapse_triangulate = True
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            print(f"DECIMATE_OK|{face_count}|{len(obj.data.polygons)}", flush=True)
        if len(obj.data.polygons) > max_faces:
            raise RuntimeError(f"ลด Mesh ไม่ถึง {max_faces:,} faces: {len(obj.data.polygons)}")
        if vertex_color_only:
            # TripoSplat should use geometry for the clean silhouette and use
            # baked textures for detail. Smooth voxel/Splat noise without
            # shrinking the overall prop volume.
            smooth = obj.modifiers.new(name="SnapGen smooth TripoSplat surface", type="LAPLACIANSMOOTH")
            smooth.lambda_factor = 0.24
            smooth.iterations = 12
            smooth.use_volume_preserve = True
            smooth.use_normalized = True
            bpy.ops.object.modifier_apply(modifier=smooth.name)
            print(f"SMOOTH_OK|{len(obj.data.polygons)}", flush=True)
            # Restore source color after topology is final. This preserves
            # small texture details on food, fabric, labels, and other props.
            if not obj.data.color_attributes:
                obj.data.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
            transfer = obj.modifiers.new(name="SnapGen restore final color", type="DATA_TRANSFER")
            transfer.object = original
            transfer.use_loop_data = True
            transfer.data_types_loops = {"COLOR_CORNER"}
            transfer.loop_mapping = "POLYINTERP_NEAREST"
            bpy.ops.object.modifier_apply(modifier=transfer.name)
            bpy.data.objects.remove(original, do_unlink=True)
            print(f"COLOR_RESTORE_OK|{len(obj.data.polygons)}", flush=True)
    texture_dir = output.with_suffix(".fbm")
    texture_dir.mkdir(parents=True, exist_ok=True)
    for stale in texture_dir.iterdir():
        if stale.is_file() and stale.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            stale.unlink()
    if not any(image.size[0] > 0 and image.size[1] > 0 for image in bpy.data.images):
        for obj in meshes:
            _bake_vertex_color(obj, texture_dir)
    for index, image in enumerate(list(bpy.data.images)):
        if (image.source == "VIEWER" or image.size[0] < 1 or image.size[1] < 1
                or image.get("snapgen_baked_vertex_color")):
            continue
        is_base_color = any(
            node.type == "TEX_IMAGE" and node.image == image
            and any(link.to_socket.name == "Base Color" for link in node.outputs["Color"].links)
            for material in bpy.data.materials if material.node_tree
            for node in material.node_tree.nodes
        )
        packed_path = texture_dir / f"source_{index}.png"
        if image.packed_file:
            packed_path.write_bytes(image.packed_file.data)
        else:
            image.save_render(str(packed_path))
        loaded_image = bpy.data.images.load(str(packed_path), check_existing=False)
        if is_base_color:
            texture_path = texture_dir / "BaseColor.png"
            _save_rgb(loaded_image, texture_path)
        else:
            texture_path = texture_dir / "PBR.png"
            texture_path.write_bytes(packed_path.read_bytes())
        if not texture_path.is_file() or texture_path.stat().st_size == 0:
            raise RuntimeError(f"บันทึก Texture ไม่สำเร็จ: {image.name}")
        print(f"TEXTURE_OK|{texture_path}|{texture_path.stat().st_size}", flush=True)
        file_image = bpy.data.images.load(str(texture_path), check_existing=False)
        for material in bpy.data.materials:
            if not material.node_tree:
                continue
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image == image:
                    node.image = file_image
        if not is_base_color:
            roughness_image = _save_channel(loaded_image, 1, texture_dir / "Roughness.png")
            metallic_image = _save_channel(loaded_image, 2, texture_dir / "Metallic.png")
            for material in bpy.data.materials:
                if not material.node_tree:
                    continue
                nodes = material.node_tree.nodes
                links = material.node_tree.links
                principled = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
                if not principled:
                    continue
                for socket_name, split_image in (
                    ("Roughness", roughness_image), ("Metallic", metallic_image)
                ):
                    for link in list(principled.inputs[socket_name].links):
                        links.remove(link)
                    texture = nodes.new("ShaderNodeTexImage")
                    texture.name = socket_name
                    texture.label = socket_name
                    texture.image = split_image
                    texture.image.colorspace_settings.name = "Non-Color"
                    links.new(texture.outputs["Color"], principled.inputs[socket_name])
            for material in bpy.data.materials:
                if not material.node_tree:
                    continue
                for node in list(material.node_tree.nodes):
                    if node.type == "TEX_IMAGE" and node.image == file_image:
                        material.node_tree.nodes.remove(node)
            if texture_path.exists():
                texture_path.unlink()
        if packed_path != texture_path and packed_path.exists():
            packed_path.unlink()
    for material in bpy.data.materials:
        if not material.node_tree:
            continue
        for node in material.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            node.inputs["Alpha"].default_value = 1.0
            for link in list(node.inputs["Alpha"].links):
                material.node_tree.links.remove(link)
    base_color_path = texture_dir / "BaseColor.png"
    if base_color_path.is_file():
        normal_image = _save_normal(base_color_path, texture_dir / "Normal.png")
        roughness_path = texture_dir / "Roughness.png"
        metallic_path = texture_dir / "Metallic.png"
        roughness_image = (
            bpy.data.images.load(str(roughness_path), check_existing=False)
            if roughness_path.is_file() else _save_constant(0.65, roughness_path)
        )
        metallic_image = (
            bpy.data.images.load(str(metallic_path), check_existing=False)
            if metallic_path.is_file() else _save_constant(0.0, metallic_path)
        )
        for material in bpy.data.materials:
            if not material.node_tree:
                continue
            nodes = material.node_tree.nodes
            links = material.node_tree.links
            principled = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
            if not principled:
                continue
            for socket_name, image in (("Roughness", roughness_image), ("Metallic", metallic_image)):
                if not principled.inputs[socket_name].is_linked:
                    texture = nodes.new("ShaderNodeTexImage")
                    texture.name = socket_name
                    texture.image = image
                    texture.image.colorspace_settings.name = "Non-Color"
                    links.new(texture.outputs["Color"], principled.inputs[socket_name])
            normal_texture = nodes.new("ShaderNodeTexImage")
            normal_texture.name = "Normal"
            normal_texture.image = normal_image
            normal_texture.image.colorspace_settings.name = "Non-Color"
            normal_map = nodes.new("ShaderNodeNormalMap")
            # TripoSplat already carries photographic surface detail in color.
            # Keep generated normal subtle so rice/plate do not look cratered.
            normal_map.inputs["Strength"].default_value = 0.12
            links.new(normal_texture.outputs["Color"], normal_map.inputs["Color"])
            links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    for obj in meshes:
        for polygon in obj.data.polygons:
            polygon.use_smooth = True
    bpy.ops.export_scene.fbx(
        filepath=str(output),
        use_selection=True,
        object_types={"MESH"},
        apply_unit_scale=True,
        bake_space_transform=False,
        add_leaf_bones=False,
        path_mode="COPY",
        embed_textures=True,
        use_mesh_modifiers=True,
        mesh_smooth_type="OFF",
        colors_type="LINEAR",
    )
    print(f"FBX_OK|{output}", flush=True)


main()


