import ast
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "snapgen_modules"))
import snapgen_seedance_video as S
from snapgen_mobile_controller import MobileController


def image(tmp_path, name):
    path = tmp_path / (name + ".png")
    path.write_bytes(b"test reference")
    return path


@pytest.mark.parametrize("model,durations", [("sd-2-lower", ("5", "10", "15")),
                                             ("sd-2-5-lower", ("5", "10", "30"))])
def test_provider_contract_and_reference_order(tmp_path, model, durations):
    scene = image(tmp_path, "ฉากทะเล")
    actor = image(tmp_path, "แก้วมัจฉา")
    location = image(tmp_path, "ถ้ำ")
    refs = [{"path": actor, "name": "แก้วมัจฉา", "role": "ตัวละคร"},
            {"path": location, "name": "ถ้ำ", "role": "สถานที่"}]
    for duration in durations:
        for aspect in S.ASPECTS:
            cmd = S.build_command("test-key", model, "@รักษาพรอมต์", scene, "1080p", duration, aspect, refs)
            assert cmd[cmd.index("POST") + 1] == S.API_URL
            assert "model=" + model in cmd and "duration=" + duration in cmd
            assert "resolution=720p" in cmd and "aspect_ratio=" + aspect in cmd
            assert "mode=lower" in cmd and "mode_image=ingredient" in cmd
            assert [part for part in cmd if part.startswith("ref_images=@")] == [
                "ref_images=@" + str(path) for path in (scene, actor, location)]
            prompt = next(part for part in cmd if part.startswith("prompt="))
            assert "@image1: รูปฉากหลัก" in prompt
            assert "@image2: Ref ตัวละคร: แก้วมัจฉา" in prompt
            assert "@image3: Ref สถานที่: ถ้ำ" in prompt
            assert prompt.endswith("@รักษาพรอมต์")


def test_references_fail_before_submit_instead_of_silently_dropping(tmp_path):
    scene = image(tmp_path, "scene")
    refs = [{"path": image(tmp_path, str(i)), "role": "ตัวละคร"} for i in range(4)]
    with pytest.raises(ValueError, match="4 รูป"):
        S.build_command("key", "sd-2-lower", "test", scene, "720p", "5", "16:9", refs)
    with pytest.raises(RuntimeError, match="ไม่พบรูปอ้างอิง"):
        S.reference_prompt("test", scene, [{"path": tmp_path / "missing.png", "role": "ตัวละคร"}])
    with pytest.raises(ValueError, match="ประเภท"):
        S.reference_prompt("test", scene, [{"path": scene, "role": "unknown"}])


def test_reference_store_restart_and_scene_isolation(tmp_path):
    first, second = image(tmp_path, "first"), image(tmp_path, "second")
    actor = image(tmp_path, "actor")
    refs = [{"path": str(actor), "role": "ตัวละคร", "name": "actor"}]
    path = tmp_path / "references.json"
    S.ReferenceStore(path).save(first, refs)
    restarted = S.ReferenceStore(path)
    assert restarted.get(first) == refs
    assert restarted.get(second) == []
    assert json.loads(path.read_text(encoding="utf-8"))[restarted.key(first)] == refs
    restarted.get(first)[0]["role"] = "สถานที่"
    assert restarted.get(first) == refs


def test_auto_selects_only_this_shot_and_uses_explicit_roles(tmp_path):
    scene = {"image": image(tmp_path, "scene"), "characters": ["นางเงือก"], "location": "ถ้ำ"}
    candidates = [(name, image(tmp_path, name)) for name in ("นางเงือก", "นางยักษ์", "ถ้ำ")]
    refs = S.scene_references(scene, candidates)
    assert [(item["name"], item["role"]) for item in refs] == [("นางเงือก", "ตัวละคร"), ("ถ้ำ", "สถานที่")]
    with pytest.raises(ValueError, match="ไม่พบ Ref ตัวละคร"):
        S.scene_references(scene, [("นางยักษ์", candidates[1][1])])
    assert S.auto_clip_settings("sd-2-lower", {"shot_seconds": 20, "clip_slow": True}) == ("sd-2-lower", 10, True)
    assert S.auto_clip_settings("sd-2-5-lower", {"shot_seconds": 12, "dialogue": "พูด", "clip_slow": True}) == ("sd-2-5-lower", 30, False)
    with pytest.raises(ValueError, match="แบ่งช็อต"):
        S.auto_clip_settings("sd-2-lower", {"shot_seconds": 20})


def test_mobile_options_keep_models_distinct():
    controller = MobileController.__new__(MobileController)
    controller.app = {}
    controller.g = {"duration_values_for_model": lambda model: ["8"]}
    models = {item["value"] for item in controller._models()}
    assert set(S.MODELS) <= models
    for model in S.MODELS:
        options = controller.options(model)
        assert options["duration"] == list(S.DURATIONS[model])
        assert options["resolution"] == ["720p"]
        assert options["aspect"][1:] == list(S.ASPECTS)
    assert controller.options("grok-lower")["duration"] == ["6", "10", "15"]


def launcher_tree():
    return ast.parse((ROOT / "snapgen_gui_v2.py").read_text(encoding="utf-8-sig"))


def test_saved_slot_restores_seedance_instead_of_recovered_default():
    class Var:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
    block = next(node for node in launcher_tree().body if isinstance(node, ast.Try)
                 and any(isinstance(item, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_local_model_ids"
                                                            for t in item.targets) for item in node.body))
    saved = [{"model": model, "resolution": "720p", "duration": S.DURATIONS[model][-1], "aspect": "21:9"}
             for model in S.MODELS]
    slots = [{key: Var("old") for key in item} for item in saved]
    ns = {"_saved_local_slot_configs": saved, "g": {"slot_cfg_vars": slots},
          "SEEDANCE_MODELS": S.MODELS, "_normalize_seedance_settings": S.normalize_settings}
    exec(compile(ast.Module(body=[block], type_ignores=[]), "launcher-startup", "exec"), ns)
    assert [{key: var.get() for key, var in item.items()} for item in slots] == saved


def test_dispatch_sends_seedance_references_and_leaves_grok_route(tmp_path):
    var = lambda value: SimpleNamespace(get=lambda: value)
    scene, actor = image(tmp_path, "scene"), image(tmp_path, "actor")
    refs = [{"path": actor, "role": "ตัวละคร"}]
    calls = []
    cfg = {key: var(value) for key, value in dict(model="sd-2-5-lower", resolution="720p",
                                                duration="30", aspect="3:4", mode="custom").items()}
    g = {"slot_cfg_vars": [cfg], "api_key_var": var("test-key"),
         "run_json": lambda cmd, **kw: calls.append(cmd) or {"uuid": "test-job"}}
    node = next(node for node in ast.walk(launcher_tree()) if isinstance(node, ast.FunctionDef)
                and node.name == "_generate_one_with_grok_lower")
    ns = dict(g=g, GROK_LOWER_MODEL="grok-lower", SEEDANCE_MODELS=S.MODELS,
              _video_submit_context=SimpleNamespace(seedance_references=refs),
              _build_seedance_command=S.build_command, _original_generate_one=lambda *args: "old-route")
    exec(compile(ast.Module(body=[node], type_ignores=[]), "dispatch", "exec"), ns)
    assert ns[node.name](0, scene, "test") == {"uuid": "test-job"}
    assert "model=sd-2-5-lower" in calls[0] and "ref_images=@" + str(actor) in calls[0]
    cfg["model"] = var("veo-3.1-lite")
    assert ns[node.name](0, scene, "test") == "old-route"


def test_auto_model_selection_save_then_restart_load(tmp_path):
    class Var:
        def __init__(self, value):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
    tree = ast.parse((ROOT / "snapgen_modules" / "snapgen_page_narrate.py").read_text(encoding="utf-8-sig"))
    names = {"video_model_changed", "save_project", "show_project"}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    state = {"project": {}, "folder": str(tmp_path), "lock": threading.RLock()}
    ns = dict(state=state, Path=Path, json=json, os=__import__("os"), SEEDANCE_MODELS=S.MODELS,
              video_model_var=Var(S.MODELS["sd-2-5-lower"]), project_path=lambda: tmp_path / "project.json",
              refresh_clip_info=lambda: None, last_project_file=lambda: tmp_path / "last.txt", video_mode=True,
              STYLES={}, last_style=lambda: "ปกติ", refresh_all=lambda: None)
    for key, value in dict(script_var="", audio_var="", aspect_var="16:9", count_var="36", subtitle_var=False,
                           economy_var=True, film_var=False, style_var="ปกติ").items():
        ns[key] = Var(value)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "auto-persistence", "exec"), ns)
    ns["video_model_changed"]()
    persisted = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert persisted["video_model"] == "sd-2-5-lower"
    ns["video_model_var"] = Var("อัตโนมัติ (Grok / Vela)")
    ns["show_project"](tmp_path, persisted)
    assert ns["video_model_var"].get() == S.MODELS["sd-2-5-lower"]


def test_desktop_menu_uses_each_model_capabilities():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        cfg = {key: tk.StringVar(root, value=value) for key, value in
               dict(model="sd-2-5-lower", resolution="1080p", duration="15", aspect="21:9").items()}
        menus = {key: tk.OptionMenu(root, var, var.get()) for key, var in cfg.items()}
        g = {"slot_cfg_vars": [cfg]}
        ns = dict(g=g, root=root, SEEDANCE_MODELS=S.MODELS, SEEDANCE_DURATIONS=S.DURATIONS,
                  SEEDANCE_ASPECTS=S.ASPECTS, _normalize_seedance_settings=S.normalize_settings,
                  GROK_LOWER_DURATIONS=("6", "10", "15"), VIDEO_AUTO_ASPECT="อัตโนมัติ",
                  VIDEO_AUTO_ASPECT_LABEL="อัตโนมัติ (ตามรูป)")
        for prefix, model in dict(MINIMAX_H3_LOCAL="minimax-h3-local", LTX23_LOCAL="ltx-2.3-local",
                                  LTX25_MAESTRO="ltx-2.5-maestro", LTX23_CLOUD="ltx-2.5-cloud",
                                  VELA_VIDEO="vela-ai-video", GROK_LOWER="grok-lower").items():
            ns[prefix + "_MODEL"] = model
            ns[prefix + "_LABEL"] = model
        node = next(node for node in launcher_tree().body if isinstance(node, ast.FunctionDef)
                    and node.name == "_add_minimax_h3_to_model_menu")
        exec(compile(ast.Module(body=[node], type_ignores=[]), "desktop-menu", "exec"), ns)
        def labels(key):
            menu = menus[key]["menu"]
            return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
                    if menu.type(i) != "separator"]
        ns[node.name](0)
        assert set(S.MODELS.values()) <= set(labels("model"))
        assert "แนบ Ref Seedance (ฉาก / ตัวละคร)" in labels("model")
        assert labels("duration") == ["5", "10", "30"]
        assert labels("resolution") == ["720p"]
        assert labels("aspect") == ["อัตโนมัติ", *S.ASPECTS]
        assert cfg["resolution"].get() == "720p" and cfg["duration"].get() == "5"
        cfg["model"].set("sd-2-lower")
        ns[node.name](0)
        assert labels("duration") == ["5", "10", "15"]
    finally:
        root.destroy()
