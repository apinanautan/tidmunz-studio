# -*- coding: utf-8 -*-
"""ตัดเสียง page: a storyboard sheet for one saved part of the story's narration.

Every part of one source audio file goes into the same GPT chat (kept in
audio_output/storyboard/storyboard_history.json), so part 5 is planned knowing
parts 1-4 and the camera, characters and places stay continuous.
Results: audio_output/storyboard/<part>.png (sheet) and <part>.txt (shot plan).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

_lock = threading.Lock()


def folder(source: Path) -> Path:
    return source.parent / "audio_output" / "storyboard"


def _history_file(source: Path) -> Path:
    return folder(source) / "storyboard_history.json"


def _load(source: Path) -> dict:
    try:
        data = json.loads(_history_file(source).read_text(encoding="utf-8"))
        entry = data.get(str(source.resolve()))
        return entry if isinstance(entry, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(source: Path, entry: dict) -> None:
    path = _history_file(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        data = {}
    data[str(source.resolve())] = entry
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def outputs(source: Path, number: int) -> tuple[Path, Path]:
    return folder(source) / f"{number}.png", folder(source) / f"{number}.txt"


def done_parts(source: Path) -> set[int]:
    return {n for n in range(1, 21) if outputs(source, n)[0].is_file()}


def transcribe(audio: Path, log) -> str:
    from snapgen_page_narrate import transcribe_in_background
    texts = []
    for item in transcribe_in_background(str(audio)):
        if item.get("log"):
            log(str(item["log"]))
        elif item.get("text"):
            texts.append(str(item["text"]).strip())
    return " ".join(t for t in texts if t)


def _chat(g: dict, conversation: dict, content: str) -> str:
    body = {"model": "auto", "chatgpt_image_intercept": False, "temperature": 0.4,
            "messages": [{"role": "user", "content": content}]}
    if conversation.get("conversation_id") and conversation.get("parent_message_id"):
        body["metadata"] = {"conversation_id": conversation["conversation_id"],
                            "parent_message_id": conversation["parent_message_id"]}
        if conversation.get("account_alias"):
            body["chatgpt_account"] = conversation["account_alias"]
    request = urllib.request.Request(
        g["_chatgpt_api_base"]() + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer local-dev-key", "Content-Type": "application/json; charset=utf-8"},
        method="POST")
    with (g.get("_bridge_queue_lock") or threading.Lock()):
        wait_free = g.get("_wait_bridge_free")
        if callable(wait_free):
            wait_free()
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Bridge HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:400]}") from exc
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"], ensure_ascii=False)[:400])
    conversation_id, parent_id = g["_extract_bridge_cursor"](data)
    if conversation_id and parent_id:
        conversation.update(conversation_id=str(conversation_id), parent_message_id=str(parent_id))
        if data.get("chatgpt_account"):
            conversation["account_alias"] = str(data["chatgpt_account"])
    message = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if isinstance(message, list):
        message = "".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in message)
    return str(message).strip()


def _intro(title: str) -> str:
    return (
        f"แชตนี้ใช้วางสตอรี่บอร์ดนิทานแอนิเมชัน 3D เรื่อง \"{title}\" ทีละท่อนเสียงบรรยาย\n"
        "ฉันจะส่งคำบรรยายของแต่ละท่อนมาตามลำดับ (ท่อน 1, 2, 3, ...) ให้จำตัวละคร หน้าตา ชุด สถานที่ "
        "และทิศทางกล้องของท่อนก่อน ๆ ในแชตนี้ แล้วทำท่อนต่อไปให้ต่อเนื่องกัน\n"
        "ตอบรับสั้น ๆ ว่าพร้อม")


def _plan_request(number: int, narration: str, earlier: list[int]) -> str:
    before = f" (ต่อจากท่อน {', '.join(map(str, earlier))} ที่ทำไว้แล้วในแชตนี้)" if earlier else ""
    return (
        f"ท่อนที่ {number}{before}\nคำบรรยายของท่อนนี้:\n\"\"\"\n{narration}\n\"\"\"\n\n"
        "วางแผนช็อตสำหรับทำแอนิเมชัน 3D ของท่อนนี้ 4-6 ช็อต เรียงตามเวลาของคำบรรยาย ตอบเป็นภาษาไทย ทีละช็อต:\n"
        "- ช็อตที่ / คำบรรยายช่วงไหน\n"
        "- มุมกล้องและขนาดภาพ (เช่น wide / medium / close-up, มุมสูง / ระดับสายตา / มุมต่ำ) และเลนส์โดยประมาณ\n"
        "- กล้องเคลื่อนยังไง (นิ่ง / แพน / ดอลลี่เข้า / ตามตัวละคร)\n"
        "- ตัวละครอยู่ตรงไหน หันทางไหน ทำท่าอะไร สีหน้าอะไร\n"
        "- ฉาก แสง และอารมณ์\n"
        "- เคล็ดลับทำใน 3D สั้น ๆ (จัดฉาก / ท่าทาง / จังหวะ)\n"
        "ให้ต่อเนื่องกับท่อนก่อนหน้า (ตัวละครหน้าตา ชุด ฉากเดิม และทิศทางกล้องไม่กระโดดข้ามเส้น)")


def _sheet_prompt(number: int, plan: str) -> str:
    return (
        f"Draw the STORYBOARD SHEET of part {number} from this shot plan:\n\"\"\"\n{plan[:6000]}\n\"\"\"\n"
        "One landscape page with one panel per shot, in shot order, each panel showing that shot's exact camera "
        "angle, framing and the characters' positions and poses, as clean 3D-animation previs frames "
        "(simple shaded 3D look, same characters and places as the earlier parts of this chat). "
        "Under each panel write only the shot number and the camera size in a few words (e.g. '1 · WIDE'). "
        "No other text.")


def make(g: dict, source: Path, number: int, audio: Path, title: str, log, redo: bool = False) -> tuple[str, Path]:
    """Transcribe the part, plan its shots and draw its storyboard sheet in the story's chat."""
    image_path, plan_path = outputs(source, number)
    if not redo and image_path.is_file() and plan_path.is_file():
        return plan_path.read_text(encoding="utf-8"), image_path
    with _lock:  # one storyboard at a time: they share one chat
        entry = _load(source)
        conversation = entry.setdefault("conversation", {})
        parts = entry.setdefault("parts", {})
        log(f"สตอรี่บอร์ดท่อน {number}: ฟังเสียง…")
        narration = transcribe(audio, log)
        if not narration:
            raise RuntimeError("ฟังเสียงท่อนนี้ไม่ออก (ไม่มีคำบรรยาย)")
        if not conversation.get("conversation_id"):
            log("เริ่มแชตสตอรี่บอร์ดของเรื่องนี้")
            _chat(g, conversation, _intro(title))
            _save(source, entry)
        earlier = sorted(int(n) for n in parts if int(n) != number)
        log(f"สตอรี่บอร์ดท่อน {number}: ให้ GPT วางมุมกล้อง…")
        plan = _chat(g, conversation, _plan_request(number, narration, earlier))
        _save(source, entry)
        folder(source).mkdir(parents=True, exist_ok=True)
        plan_path.write_text(f"ท่อน {number}\n\nคำบรรยาย:\n{narration}\n\n{plan}\n", encoding="utf-8")
        log(f"สตอรี่บอร์ดท่อน {number}: วาดสตอรี่ชีต…")
        imgmod = (g.get("_runtime_g") or {}).get("_imgmod") or g.get("_imgmod")
        if imgmod is None:
            raise RuntimeError("ระบบสร้างรูปยังไม่พร้อม")
        out = Path(imgmod.generate_image(
            _sheet_prompt(number, plan), output_dir=str(folder(source)), name_hint=f"sheet_{number}",
            aspect_ratio="16:9", save_sidecar=False, conversation_state=conversation,
            conversation_save_fn=lambda: _save(source, entry)))
        image_path.unlink(missing_ok=True)
        out.replace(image_path)
        parts[str(number)] = {"audio": str(audio), "narration": narration}
        _save(source, entry)
        return plan_path.read_text(encoding="utf-8"), image_path
