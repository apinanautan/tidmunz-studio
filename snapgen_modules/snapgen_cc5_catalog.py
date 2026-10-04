"""Catalog of the user's own Character Creator clothes, hair and accessories.

Every Reallusion content file (.ccCloth, .ccHair, .rlHair, .ccShoes, .ccAcc,
.ccGloves, .iCloth, .iHair, .iAcc) embeds a preview render. File names are
unreliable ("shirt.ccCloth" can be a doctor's coat), so each item is judged
from its picture: GPT looks at numbered contact sheets and names every piece
for Thai folk-tale (นิทานพื้นบ้าน) use.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import urllib.request
from pathlib import Path

ASSET_KINDS = {
    ".cccloth": "cloth", ".icloth": "cloth",
    ".ccshoes": "shoes",
    ".ccgloves": "gloves",
    ".cchair": "hair", ".rlhair": "hair", ".ihair": "hair",
    ".ccacc": "accessory", ".iacc": "accessory",
}
SHEET_SIZE = 20
THUMB = 224


def scan(root):
    """Every asset file under root, one entry per (name, size) to skip copies."""
    items, seen = [], set()
    for path in sorted(Path(root).rglob("*")):
        kind = ASSET_KINDS.get(path.suffix.casefold())
        if not kind or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        key = (path.name.casefold(), size)
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "id": hashlib.sha1(f"{path.name}|{size}".encode("utf-8")).hexdigest()[:16],
            "path": str(path), "file": path.name, "kind": kind, "size": size,
            "folder": str(path.parent.relative_to(root)),
        })
    return items


def extract_thumbnail(path):
    """The largest embedded JPEG/PNG preview that decodes, as RGB PIL image (or None)."""
    from PIL import Image

    data = Path(path).read_bytes()
    candidates = []
    start = 0
    while True:
        i = data.find(b"\xff\xd8\xff", start)
        if i < 0:
            break
        j = data.find(b"\xff\xd9", i)
        if j < 0:
            break
        candidates.append(data[i:j + 2])
        start = i + 3
    start = 0
    while True:
        i = data.find(b"\x89PNG\r\n\x1a\n", start)
        if i < 0:
            break
        j = data.find(b"IEND", i)
        if j < 0:
            break
        candidates.append(data[i:j + 8])
        start = i + 8
    # The preview render is the first picture in the file (right after the
    # header); later pictures are textures (UV layouts, opacity maps).
    # A preview has a gray studio background; textures have black/flat corners.
    candidates.sort(key=lambda blob: data.find(blob[:64]))
    first = None
    for blob in candidates:
        try:
            image = Image.open(io.BytesIO(blob))
            image.load()
        except Exception:
            continue
        if min(image.size) < 96:
            continue
        image = image.convert("RGB")
        first = first or image
        if _looks_like_preview(image):
            return image
    return None if first is None or not _looks_like_preview(first) else first


def _looks_like_preview(image):
    from PIL import ImageStat

    # Previews are square-ish renders of 256-1024 px; texture maps are tiny
    # 160 px icons or 2048+ px sheets.
    if not 200 <= min(image.size) <= 1100 or max(image.size) > 1.3 * min(image.size):
        return False
    small = image.resize((64, 64))
    corners = [small.getpixel(p) for p in ((1, 1), (62, 1), (1, 62), (62, 62))]
    corner_level = sum(sum(c) / 3 for c in corners) / 4
    spread = max(ImageStat.Stat(small.convert("L")).stddev)
    return 20 <= corner_level <= 235 and spread > 8


def contact_sheet(images):
    """Numbered grid (4 columns) so GPT can answer per number."""
    from PIL import Image, ImageDraw, ImageFont

    cols = 5
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * THUMB, rows * THUMB), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 40)
    except Exception:
        font = ImageFont.load_default()
    for index, image in enumerate(images):
        tile = image.copy()
        tile.thumbnail((THUMB, THUMB))
        x, y = (index % cols) * THUMB, (index // cols) * THUMB
        sheet.paste(tile, (x + (THUMB - tile.width) // 2, y + (THUMB - tile.height) // 2))
        draw.rectangle([x, y, x + 54, y + 46], fill=(255, 220, 0))
        draw.text((x + 6, y + 2), str(index + 1), fill=(0, 0, 0), font=font)
    return sheet


CLASSIFY_PROMPT = """คุณคือคนคัดเครื่องแต่งกายสำหรับการ์ตูน 3D "นิทานพื้นบ้านไทย" (ชาวบ้าน ชนบท ไทยโบราณ เรียบง่าย).
รูปนี้มี {count} ช่อง มีเลขสีเหลืองกำกับ แต่ละช่องคือของ 1 ชิ้นจาก Character Creator (ประเภทจากนามสกุลไฟล์: {kinds}).
ดูจากรูปจริงเท่านั้น (ชื่อไฟล์เชื่อไม่ได้) แล้วตอบ JSON array เท่านั้น ห้ามมีข้อความอื่น ช่องละ 1 object ตามลำดับเลข:
{{"n":1,"name_th":"ชื่อไทยสั้นที่บอกว่าเป็นอะไรจริง เช่น เสื้อคอกลมผ้าฝ้ายสีน้ำตาล",
"part":"upper|lower|full|outer|shoes|hair|beard|brows|hat|jewelry|bag|other",
"garment":"ชนิดเจาะจง เช่น เสื้อคอกลม/เสื้อม่อฮ่อม/ผ้าถุง/โจงกระเบน/กางเกงเล/สไบ/ผ้าขาวม้า/ผมมวย/ผมสั้นเกรียน",
"gender":"ชาย|หญิง|ได้ทั้งคู่","age":"เด็ก|หนุ่มสาว|วัยกลางคน|คนแก่|ทุกวัย",
"style":"ไทยโบราณ|ชาวบ้านชนบท|ร่วมสมัยเรียบง่าย|ทันสมัย|ชุดทำงาน/เครื่องแบบ|แฟนตาซี/ไซไฟ/เกราะ|ต่างชาติ",
"folk_fit":0-3 (3=เหมาะมากกับนิทานพื้นบ้านไทย ใส่แล้วเรียบง่ายดูเป็นชาวบ้านไทย, 2=ใช้ได้ถ้าเรื่องเป็นยุคปัจจุบันเรียบง่าย, 1=ใช้ได้เฉพาะตัวละครพิเศษ เช่น เศรษฐี ฝรั่ง หมอ, 0=ไม่ควรใช้ เช่น ไซไฟ เกราะ ชุดปาร์ตี้ วับๆแวมๆ แบรนด์),
"colors":"สีหลัก","note":"เหตุผลสั้นๆ ว่าเหมาะกับตัวละครแบบไหน"}}
ตัดสินเข้ม: ของหวือหวา โชว์เนื้อ ไซไฟ เกราะ ชุดแฟชั่นจัด = 0. ผมทรงทันสมัยจัด/ย้อมสีจัด = 0-1."""


def classify_sheet(images, kinds, bridge="http://127.0.0.1:8000/v1", api_key="local-dev-key", timeout=600):
    sheet = contact_sheet(images)
    buffer = io.BytesIO()
    sheet.save(buffer, "JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    body = {
        "model": "auto", "temperature": 0.1, "chatgpt_image_intercept": False,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": CLASSIFY_PROMPT.format(count=len(images), kinds=", ".join(kinds))},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }
    request = urllib.request.Request(
        bridge.rstrip("/") + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8", errors="replace"))
    content = ((result.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if isinstance(content, list):
        content = "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in content)
    match = re.search(r"\[.*\]", str(content), re.S)
    if not match:
        raise RuntimeError("GPT ไม่ได้ตอบ JSON: " + str(content)[:300])
    rows = json.loads(match.group(0))
    return {int(row.get("n", 0)): row for row in rows if isinstance(row, dict)}


def upload_quota_wait(bridge="http://127.0.0.1:8000/v1", api_key="local-dev-key"):
    """Seconds until ChatGPT file_upload quota resets when it is used up, else 0."""
    import datetime
    try:
        request = urllib.request.Request(bridge.rstrip("/") + "/chatgpt/usage",
                                         headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            usage = json.loads(response.read().decode("utf-8"))
        feature = (usage["accounts"][0].get("features") or {}).get("file_upload") or {}
        if feature.get("remaining") != 0 or not feature.get("reset_after"):
            return 0
        reset = datetime.datetime.fromisoformat(str(feature["reset_after"]))
        return max(60, int((reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) + 60)
    except Exception:
        return 0


def build(root, out_dir, limit=0, log=print):
    """Scan root, extract thumbnails, classify; resumable (saves after every sheet)."""
    out_dir = Path(out_dir)
    thumbs = out_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    catalog_path = out_dir / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else {"items": {}}
    items = scan(root)
    log(f"พบ {len(items)} ชิ้น")
    pending = [item for item in items if item["id"] not in catalog["items"]]
    if limit:
        pending = pending[:limit]
    batch = []

    def flush():
        if not batch:
            return
        kinds = sorted({item["kind"] for item, _ in batch})
        answers = None
        for attempt in range(4):
            try:
                answers = classify_sheet([image for _, image in batch], kinds)
                break
            except Exception as exc:  # Bridge 502s and odd replies are transient
                log(f"GPT ล้มเหลว (ครั้งที่ {attempt + 1}): {str(exc)[:200]}")
                import time
                wait = upload_quota_wait()
                if wait:
                    log(f"โควตาอัปโหลดไฟล์ ChatGPT หมด — รอ {wait // 60} นาทีจนรีเซ็ต")
                    time.sleep(wait)
                else:
                    time.sleep(30 * (attempt + 1))
        if answers is None:
            log("ข้ามชุดนี้ไปก่อน — รันใหม่ภายหลังจะทำต่อเฉพาะที่ขาด")
            batch.clear()
            return
        for number, (item, _) in enumerate(batch, 1):
            entry = dict(item)
            entry.update(answers.get(number) or {"name_th": "", "folk_fit": None, "note": "GPT ไม่ได้ตอบช่องนี้"})
            entry["thumb"] = str(thumbs / f"{item['id']}.jpg")
            catalog["items"][item["id"]] = entry
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"จัดหมวดแล้ว {len(catalog['items'])} ชิ้น")
        batch.clear()

    for item in pending:
        thumb_path = thumbs / f"{item['id']}.jpg"
        try:
            if thumb_path.is_file():
                from PIL import Image
                image = Image.open(thumb_path).convert("RGB")
            else:
                image = extract_thumbnail(item["path"])
                if image is None:
                    # Never guess from texture maps; this piece needs a CC5 render.
                    catalog["items"][item["id"]] = dict(item, status="no_preview", folk_fit=None,
                                                        name_th="", note="ไฟล์ไม่มีภาพตัวอย่าง ต้องเรนเดอร์ใน CC5")
                    log(f"ไม่มีรูปตัวอย่าง: {item['file']}")
                    continue
                image.thumbnail((512, 512))
                image.save(thumb_path, "JPEG", quality=88)
        except OSError as exc:
            log(f"อ่านไฟล์ไม่ได้ {item['file']}: {exc}")
            continue
        batch.append((item, image))
        if len(batch) >= SHEET_SIZE:
            flush()
    flush()
    return catalog


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root")
    parser.add_argument("out")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    build(args.root, args.out, args.limit)
