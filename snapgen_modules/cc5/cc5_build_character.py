# Run inside Character Creator 5 (Script > Load Python).
# Builds one character from a job folder:
#   AI face -> Headshot 3 head (unless skip_headshot) -> strip clothes/hair ->
#   put on chosen hair/clothes/shoes -> dye hair black/gray -> front render
#   -> save .ccProject + .png in the job folder.  Writes result.txt there.
# The job folder is read from <program>/snapgen_data/cc5_jobs/current_job.txt
# (one line: full path of the job folder containing job.json).
# Running it again re-dresses the current avatar, so it also swaps hair/shoes.
import json, os, time, traceback
import RLPy
import RLPy2

PROGRAM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOBS = os.path.join(PROGRAM, "snapgen_data", "cc5_jobs")
log = []


def job_dir():
    pointer = os.path.join(JOBS, "current_job.txt")
    with open(pointer, encoding="utf-8-sig") as f:
        return f.read().strip()


HERE = job_dir()


def _write():
    with open(os.path.join(HERE, "result.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log))


try:
    job = json.load(open(os.path.join(HERE, "job.json"), encoding="utf-8"))
except Exception:
    log.append("FAIL job.json\n" + traceback.format_exc()); _write(); raise


def step(name, fn):
    t = time.time()
    try:
        r = fn()
        log.append("OK   %s (%.1fs) %s" % (name, time.time() - t, "" if r is None else r))
        return r
    except Exception:
        log.append("FAIL %s\n%s" % (name, traceback.format_exc()))
        return None


def body_type():
    name = {"male": "Male", "female": "Female", "neutral": "Neutral", "child": "Child", "baby": "Baby"}.get(
        job.get("body", "neutral"), "Neutral")
    return getattr(RLPy2.EHSBodyType, name, RLPy2.EHSBodyType.Neutral)


def headshot():
    skin = RLPy2.RArrayListWString(3, "None"); skin[0] = "Soft Skin"
    mask = RLPy2.RArrayListWString(4, "None"); mask[0] = "No Mask"
    return RLPy2.Headshot3.GetInterface().CenerateCharacter(
        os.path.join(HERE, job["face"]), body_type(), False, RLPy2.RArrayListWString(), skin, mask)


if not job.get("skip_headshot"):
    step("headshot", headshot)
avatar = RLPy.RScene.GetAvatars()[0]


def strip():
    gone = []
    for obj in list(avatar.GetClothes()) + list(avatar.GetHairs()) + list(avatar.GetAccessories()):
        gone.append(obj.GetName())
        RLPy.RScene.RemoveObject(obj)
    return gone


step("strip", strip)
for item in job["items"]:
    def load(item=item):
        RLPy.RScene.SelectObject(avatar)
        return RLPy.RFileIO.LoadFile(os.path.join(HERE, item))
    step("load " + item, load)

rgb = job.get("hair_rgb", [18, 16, 15])


def dye_hair():
    # Shader colors take 0..1; dye layers and the built-in color changer would tint it brown.
    c01 = tuple(v / 255.0 for v in rgb)
    out = []
    for hair in avatar.GetHairs():
        comp = hair.GetMaterialComponent()
        for mesh in hair.GetMeshNames():
            for mat in comp.GetMaterialNames(mesh):
                comp.AddDiffuseKey(RLPy.RKey(), mesh, mat, RLPy.RRgb(*c01))
                for p in comp.GetShaderParameterNames(mesh, mat):
                    if p in ("RootColor", "TipColor", "_1st Dye Color", "_2nd Dye Color"):
                        comp.SetShaderParameter(mesh, mat, p, c01)
                    elif p in ("_1st Dye Strength", "_2nd Dye Strength", "ActiveChangeHairColor"):
                        comp.SetShaderParameter(mesh, mat, p, (0.0,))
                out.append(mesh)
        hair.Update()
    return out


step("dye hair", dye_hair)
log.append("wearing: %r" % ([o.GetName() for o in list(avatar.GetClothes()) + list(avatar.GetHairs())],))


def frame():
    RLPy.RScene.SelectObject(avatar)
    RLPy.RScene.GetCurrentCamera().SetCameraLocation(RLPy.ECameraLocationType_Front)


step("camera front", frame)
name = job["name"]  # check files stay in the job folder; only the .iAvatar goes to the story folder
step("render", lambda: RLPy.RGlobal.RenderImage(os.path.join(HERE, name + ".png")))


def save():
    setting = RLPy.RSaveFileSetting()
    setting.SetSaveType(RLPy.ESaveFileType_Character)
    return RLPy.RFileIO.SaveFile(avatar, setting, os.path.join(HERE, name + ".ccProject"))


step("save", save)
_write()
