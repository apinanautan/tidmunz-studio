# List CC5 library items as text (no pictures), so an agent can choose clothes/hair cheaply.
#   python cc5_find.py [hair|cloth|shoes|accessory] [ชาย|หญิง] [เด็ก|หนุ่มสาว|วัยกลางคน|ผู้สูงอายุ] [--all]
# Default shows only items marked curated (checked by eye). --all also shows unchecked ones.
# Also prints wardrobe status (ok / broken / rejected / used_by) from snapgen_data/cc5_wardrobe.json.
import json, os, sys

PROGRAM = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.path.isfile(os.path.join(PROGRAM, "cc5_catalog", "catalog.json")):
    # Run from the source repo: the data lives in the installed program.
    PROGRAM = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tidmunz Studio")
DATA = os.path.join(PROGRAM, "snapgen_data")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snapgen_cc5_catalog import load_catalog


def main(args):
    show_all = "--all" in args
    args = [a for a in args if a != "--all"]
    kind = next((a for a in args if a in ("hair", "cloth", "shoes", "accessory", "gloves")), None)
    gender = next((a for a in args if a in ("ชาย", "หญิง")), None)
    age = next((a for a in args if a in ("เด็ก", "หนุ่มสาว", "วัยกลางคน", "ผู้สูงอายุ")), None)
    catalog = load_catalog(os.path.join(PROGRAM, "cc5_catalog", "catalog.json"),
                           os.path.join(DATA, "cc5_hair_pool.json"))
    wardrobe = {}
    try:
        for row in json.load(open(os.path.join(DATA, "cc5_wardrobe.json"), encoding="utf-8")).get("items", []):
            wardrobe[os.path.normcase(row.get("path", ""))] = row
    except (OSError, ValueError):
        pass
    rows = 0
    for item in catalog.get("items", {}).values():
        if kind and item.get("kind") != kind:
            continue
        if not show_all and item.get("review_status") != "curated":
            continue
        g = str(item.get("gender") or "")
        if gender and gender not in g and "ทั้งคู่" not in g and "ได้ทั้ง" not in g:
            continue
        a = str(item.get("age") or "")
        if age and age not in a and "ทุกวัย" not in a:
            continue
        w = wardrobe.get(os.path.normcase(item.get("path", "")), {})
        state = w.get("status", "")
        if state in ("broken", "rejected"):
            continue  # never offer items already known to fail
        used = ",".join(w.get("used_by") or [])
        print(f"{item.get('id', '')[:8]} | {item.get('kind')}/{item.get('part', '')} | {item.get('name_th', '')} | "
              f"{g} | {a} | fit={item.get('folk_fit')} | {item.get('review_status') or '-'}"
              f"{' | ใช้แล้ว: ' + used if used else ''} | {item.get('path')}")
        rows += 1
    print(f"-- {rows} รายการ")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
