# Prompt-Ref Context System Map

เอกสารนี้เป็นแผนที่สำหรับ AI/Worker ที่ต้องอ่านหรือแก้ระบบ Prompt-Ref Context ของ SnapGen / Tidmun Studio โดยไม่ต้องไล่เดาจากทั้ง repository ก่อน

> Scope: documentation of the current working tree observed on 2026-08-08. เอกสารนี้ไม่ได้เปลี่ยน runtime behavior และไม่ได้ประกาศว่าทุก flow เป็นแบบที่ควรเป็นในอนาคต หากเอกสารกับ source ขัดกัน ให้ source ที่ checkout อยู่เป็นหลักและตรวจ Git history ก่อนแก้

## 1. TL;DR

Prompt-Ref เป็นเจ้าของ **เรื่องหลัก + Context กลาง + history/cursor ของ ChatGPT สำหรับเรื่องนั้น + Storyboard/Prompt split**

เส้นทางหลักโดยย่อ:

1. ผู้ใช้เลือก/วางบทหลัก
2. SnapGen เก็บบทที่ `snapgen_data/prompt_ref_source.txt` และ metadata ของไฟล์ต้นฉบับ
3. Prompt-Ref สร้าง ChatGPT conversation ใหม่สำหรับเรื่องนั้น
4. GPT อ่านบท แล้วสร้าง Story Breakdown / Visual Bible Context
5. Context ถูก normalize และเขียนเป็น JSON กลาง
6. Prompt-Ref ใช้ conversation เดิมต่อเพื่อสร้าง Storyboard และบันทึกผล split กลับเข้า history เดิม
7. หน้า Ref / Prop / Image อ่าน Context กลาง แต่มี conversation/history ของตัวเอง ไม่ใช้ cursor เดียวกับ Prompt-Ref

กฎสำคัญที่สุด: **หนึ่งเรื่องของ Prompt-Ref ต้องไม่หลุดไป conversation/account อื่นระหว่าง Context กับ Storyboard**

---

## 2. Entry points ที่ควรเปิดอ่านก่อน

### Core orchestration

- `snapgen_gui_v2.py`
  - Prompt-Ref conversation state: ประมาณบรรทัด 7244+
  - Context schema / audit / persistence: ประมาณ 7737-8077
  - Storyboard analysis / split / writeback: ประมาณ 8081-9220
  - Prompt-Ref UI / Context window: ประมาณ 10055-11150
  - downstream invalidation: ประมาณ 838-851

### Shared Context normalization

- `snapgen_modules/snapgen_context_tools.py`
  - อ่าน `context_master.json` / `prompt_ref_context.json`
  - normalize เป็น compatibility schema
  - health / diff / preview
  - sync `context_master.json` กลับไป `prompt_ref_context.json`

### Consumers

- `snapgen_modules/snapgen_page_ref.py`
  - อ่าน Prompt-Ref Context สำหรับ character/location reference design
  - มี Ref GPT history แยกจาก Prompt-Ref

- `snapgen_modules/snapgen_page_prop.py`
  - อ่าน `props[]` จาก Prompt-Ref Context

- `snapgen_modules/snapgen_page_image.py`
  - อ่าน source story + shared Context สำหรับ title/character picker/continuity
  - Image AI มี history แยก

- `snapgen_modules/snapgen_image_gen.py`
  - เก็บ/รีเซ็ต Image AI, Ref, Story 3D, Prop conversation state แยกกัน
  - ingest เรื่อง Prompt-Ref เข้า Image/Ref history เมื่อเริ่ม history ของหน้านั้น
  - รับ Storyboard reference เข้า Image AI history แบบ context-only

### Storyboard transport evidence/tests

- `กฏของโปรแกรม/PROMPT_REF_STORYBOARD_TRANSPORT.md`
- `tests/test_prompt_ref_single_history.py`
- `tests/test_storyboard_split_prompt_banks.py`
- `tests/test_storyboard_ref_preview.py`

### Legacy/alternate browser helper

- `tools/snapgen_prompt_ref_browser.py`
  - มี CDP helper `ask_json_in_prompt_ref()`
  - จากการค้นใน working tree ปัจจุบันยังไม่พบ caller อื่น จึงควรถือเป็น helper/alternate path ไม่ใช่ active path โดยอัตโนมัติ

---

## 3. Runtime data files

ไฟล์ด้านล่างอยู่ใต้ `snapgen_data/` เว้นแต่ระบุอย่างอื่น

| File | หน้าที่ |
|---|---|
| `prompt_ref_source.txt` | ข้อความบทหลักที่แตก/อ่านได้ในเครื่อง เป็น source ที่หลายหน้าดูชื่อเรื่องและ hash |
| `meta/prompt_ref_source_file.json` | `original_path`, `cached_path`, filename, last_dir ของไฟล์ต้นฉบับ |
| `prompt_ref_source.<ext>` | cached portable copy ของไฟล์ต้นฉบับเมื่อทำได้ |
| `meta/prompt_ref_conversation.json` | Prompt-Ref cursor/account/story/context marker |
| `prompt_ref_context.json` | shared Context ที่หน้า Ref/Prop/Image ใช้อ่าน |
| `story_breakdown.json` | categorized v4 breakdown สำหรับ inspection/debugging |
| `context_master.json` | compatibility/normalized Context สำหรับ consumer เดิม |
| `context_master.last.json` | snapshot ก่อน normalize สำหรับ diff |
| `prompt_ref_context.txt` | legacy fallback context text |
| `prompt_ref_scene_draft.txt` | draft ของ current scene ใน UI |
| `prompt_ref_storyboard_image.json` | metadata ของ Storyboard ล่าสุดและ cursor context รอบงาน |
| `prompt_ref_storyboard_direct.json` | ผล split ล่าสุดจากภาพ Storyboard |
| `prompt_ref_storyboard_pending_writeback.json` | queue fallback เมื่อ Vision สำเร็จแต่ writeback เข้า Prompt-Ref history ไม่สำเร็จ |
| `prompt_ref_storyboard_plan.json` | plan/analysis path ที่มีอยู่ใน legacy/current helper flow |
| `prompt_ref_last_director_plan.json` | director plan ล่าสุดจาก payload ที่ UI บันทึก |
| `prompt_ref_last_clip_contracts.json` | clip contracts ล่าสุดจาก `scene_slots` ถ้ามีใน payload |
| `prompt_bank_image.txt` | Image Slot bank |
| `prompt_bank_video.txt` | Video Slot bank |

อย่า commit runtime data, user story, credentials หรือ account state โดยไม่ตรวจ `.gitignore` และ requirement งานนั้นก่อน

---

## 4. Prompt-Ref conversation state

State หลักอยู่ใน:

`META = snapgen_data/meta/prompt_ref_conversation.json`

fields ที่ core loader รู้จักในปัจจุบัน:

- `conversation_id`
- `parent_message_id`
- `conversation_url`
- `account_alias`
- `chrome_profile`
- `story_hash`
- `context_ready`
- `context_conversation_id`
- `context_parent_message_id`
- `context_story_hash`
- `context_created_at`

### Readiness hierarchy

`_prompt_ref_cursor_ready()`
- ต้องมี conversation + parent

`_prompt_ref_history_ready(story)`
- cursor ต้องพร้อม
- `story_hash` ต้องตรง SHA-256 ของบทปัจจุบัน

`_prompt_ref_context_history_ready(story)`
- history ต้องพร้อม
- context marker ต้องชี้ conversation เดียวกัน
- context story hash ต้องตรง
- `prompt_ref_context.json` ต้องอ่าน/normalize ได้และ Visual Bible ต้องไม่ incomplete

ดังนั้น **มีไฟล์ Context อยู่บนดิสก์อย่างเดียวไม่พอ** ที่จะถือว่า Storyboard ต่อ history ได้

---

## 5. Story ingestion

มีหลาย helper ตาม evolution ของระบบ แต่ pattern สำคัญคือ:

### `_ingest_prompt_ref_story(full_story, ...)`

- reset Prompt-Ref conversation
- แบ่งบทเป็น ordered `input_text` chunks
- ส่งเข้า ChatGPT normal chat
- ขอ `STORY_READY`
- บันทึก `story_hash`

เหตุผลที่มี text-chunk path: ลดปัญหา Bridge/file parser บางเครื่องกับ DOCX/UTF-8 ไทย

### `_attach_docx_and_build_prompt_ref_context(...)`

- ตรวจ Bridge runtime สำหรับ DOCX
- แนบ `.docx` จริง
- ขอ `FILE_READY`
- ขอ JSON Context ใน history เดิม
- audit + mark context ready

### `_ingest_and_build_prompt_ref_context(...)`

- ส่งบทเป็นหลาย turn (`PART_OK`)
- หลังส่งครบ ขอ Context JSON
- audit + mark ready

ก่อนแก้ ingestion ต้องตรวจว่า UI ปัจจุบันเรียก path ไหนจริง ห้ามสรุปจากชื่อฟังก์ชันอย่างเดียว

---

## 6. Story Breakdown / Context schema

Prompt-Ref Context source-of-truth ฝั่ง semantic ปัจจุบันถูกสร้างเป็น **version 4 categorized breakdown**

หมวดหลัก:

- `story`
- `main_characters`
- `supporting_characters`
- `animals`
- `supernatural_entities`
- `locations`
- `props`
- `scene_map`
- `visual_rules`
- `forbidden`
- `locks`

`_normalize_prompt_ref_breakdown()` สร้าง compatibility field เพิ่ม:

- `characters` = flatten ของ main/supporting/animal/supernatural
- `breakdown_source_of_truth` = รายชื่อหมวด authoritative

### Semantic rules ที่ห้ามทำหาย

- entity type ต้องมาจาก “ตัวตนจริง” ไม่ใช่ keyword ที่มันพูดถึง
- คนเห็นผีไม่ได้แปลว่าคนนั้นเป็นผี
- animal แยกจาก human/prop
- supernatural ที่ยังไม่ยืนยันต้องคง uncertainty
- temporary state เช่น หลับ ป่วย ตาย เปียก กลัว ห้ามกลายเป็น permanent visual identity
- `needs_ref=true` เฉพาะสิ่งที่ต้องรักษาภาพจริง
- ถ้าไม่มีชื่อเฉพาะ ให้ใช้คำเรียกจากบท ไม่สร้างชื่อใหม่
- สิ่งสมมุติเพื่อภาพต้องแยกจาก evidence และติด `(สมมุติเพื่อภาพ)` ตาม policy
- Visual Bible ของ entity ที่ต้องมี Ref ต้องละเอียดพอสร้างภาพซ้ำได้

---

## 7. Context audit pipeline

`_audit_prompt_ref_context()` ทำหลายชั้น:

1. parse JSON จาก Bridge
2. normalize categorized breakdown
3. ส่ง audit turn กลับเข้า **Prompt-Ref history เดิม**
4. ถ้า Visual Bible ยังไม่ครบ ส่ง visual repair turn อีกครั้ง
5. persist `story_breakdown.json`

หลังจากนั้น UI มักเรียก `_write_context_master(data=..., invent=False)` เพื่อสร้าง compatibility Context

### Important boundary

- `story_breakdown.json` เก็บ categorized v4 view สำหรับ inspection
- `context_master.json` / `prompt_ref_context.json` ถูก normalize ผ่าน `snapgen_context_tools.py` เพื่อ compatibility กับ consumer เดิม

ห้ามถือว่าสองไฟล์นี้มี schema เหมือนกันทุก field

---

## 8. Shared Context consumers

### Ref page

Ref อ่าน Context ผ่าน `_load_ref_context()`:

1. `prompt_ref_context.json`
2. fallback `prompt_ref_context.txt`

Ref ใช้ Context เพื่อ:

- รายชื่อตัวละคร
- appearance / identity
- location targets
- reference prompt design

แต่ Ref GPT history เป็น **คนละ history** กับ Prompt-Ref

`ingest_ref_story_file()` จะ reset Ref history แล้ว ingest เรื่อง Prompt-Ref เข้า Ref history ของตัวเอง

### Prop page

Prop selector อ่าน `props[]` จาก `_load_ref_context()` / `prompt_ref_context.json`

กฎสำคัญ: selector ฝั่ง Prop ไม่ควรเอา character/location มาปนเป็น prop

### Image AI page

Image page ใช้:

- `prompt_ref_source.txt` สำหรับ story title/source
- `context_master.json` หรือ `prompt_ref_context.json` สำหรับ character list/continuity

Image AI conversation เป็น **history แยก**

เมื่อ source Prompt-Ref เปลี่ยน `invalidate_downstream_story_histories()` จะ reset Image + Ref history เพื่อไม่ให้เรื่องเก่าค้าง

### Story 3D

Architecture note และ source comment ระบุว่า Story 3D owns its cast และไม่ควรอ่าน Prompt-Ref Context เป็น cast โดยอัตโนมัติ เพราะเป็นคนละ workflow

---

## 9. Storyboard generation and split

### Preconditions

Storyboard path ต้องผ่าน `_prompt_ref_context_history_ready()`

หมายความว่า:

- story hash ตรง
- conversation เดิม
- Context marker ตรง
- Context ใช้งานได้

### Storyboard image

`_generate_prompt_ref_storyboard_image_from_scene()`

- preflight current scene ผ่าน Prompt-Ref history
- collect refs จาก Context
- สร้าง Storyboard image
- ตรวจไม่ให้ generation หลุด conversation ที่ควรผูก
- persist metadata ที่ `prompt_ref_storyboard_image.json`

### Read whole Storyboard image

Active implementation ที่พบใน working tree:

`_generate_prompts_from_storyboard_image()`

- รับ **ไฟล์เดียวกับที่ UI กำลังแสดง**
- ลดขนาดเป็น JPEG
- ส่ง Vision request เพื่ออ่าน whole storyboard
- parse JSON เป็น:
  - `panel_count`
  - `image_prompts[]`
  - `video_prompts[]`
- canonicalize character names จาก Context
- filter `matched_refs` ให้ใช้ชื่อจาก Context เท่านั้น
- normalize labels/lighting
- จากนั้น writeback JSON แบบ text-only เข้า Prompt-Ref history เดิม
- ถ้า writeback fail เก็บ `prompt_ref_storyboard_pending_writeback.json`
- persist result ที่ `prompt_ref_storyboard_direct.json`

### Split contract

- image/video arrays ต้องมีจำนวนเท่ากัน
- slot ต้องเรียงต่อเนื่อง 1..N
- `panel_count` ต้องตรงทั้งสอง arrays
- Image prompt ต้องขึ้นต้น `สร้างรูปภาพ`
- matched refs ต้อง canonical และมีอยู่ใน Context
- ภาพที่ส่งอ่านต้องเป็น path ที่ UI แสดง ไม่ใช่เดา newest file

---

## 10. History isolation contract

ห้ามรวม histories เหล่านี้มั่วกัน:

| Workflow | History |
|---|---|
| Prompt-Ref story/context/storyboard | Prompt-Ref conversation |
| Image AI | Image story conversation |
| Ref | Ref story conversation |
| Story 3D | Story Face conversation |
| Prop generation | Prop conversation/state |

Prompt-Ref Context เป็น **ข้อมูลกลาง** แต่ไม่ได้แปลว่าทุกหน้าใช้ conversation cursor เดียวกัน

นี่คือ distinction ที่สำคัญที่สุดเวลามีคนพูดว่า “ใช้ context เดียวกัน”:

- shared **data**: ใช่
- shared **ChatGPT history**: ไม่ใช่ทุกหน้า

---

## 11. Source-change invalidation

เมื่อผู้ใช้เปลี่ยน/ล้าง Prompt-Ref source:

`invalidate_downstream_story_histories()` reset:

- Image story history
- Ref story history

และล้าง title vars ที่เกี่ยวข้อง

เหตุผล: กัน Image/Ref ทำงานต่อจากเรื่องเดิมเมื่อ source story เปลี่ยนแล้ว

ก่อนเพิ่ม consumer ใหม่ ต้องกำหนดให้ชัดว่า consumer นั้นควรถูก invalidate เมื่อ source เปลี่ยนหรือไม่

---

## 12. Tests ที่เป็น contract guards

### `tests/test_prompt_ref_single_history.py`

ตรวจอย่างน้อย:

- reset ต้องล้าง context marker
- Context build ต้องไม่ silently branch chat
- continuation ต้อง reject conversation id ใหม่
- Storyboard ต้องใช้ exact history ของ Context
- analysis/prompt writing ต้อง continue history

### `tests/test_storyboard_split_prompt_banks.py`

ตรวจอย่างน้อย:

- image/video arrays แยกกัน
- canonical character naming
- slot banks ถูกต้อง
- mismatched slot numbers reject
- worker ใช้ storyboard path ที่ UI แสดง
- split/writeback ผูก Prompt-Ref state

ก่อนเปลี่ยน behavior ที่แตะ history/storyboard ควรรัน tests เหล่านี้เป็นขั้นต่ำ

---

## 13. Observed mismatches / investigation warnings

หัวข้อนี้บันทึกสิ่งที่พบจาก working tree ณ วันที่จัดทำ **ไม่ใช่คำสั่งให้แก้ทันที**

### A. Breakdown v4 vs compatibility Context v3

Prompt-Ref semantic layer สร้าง categorized v4 แต่ `snapgen_context_tools.normalize_context_master()` สร้าง `version: 3` compatibility object และ sync ไป `prompt_ref_context.json`

ผลคือโค้ดบางส่วนอาจคาด categorized fields ในขณะที่ consumer เดิมอ่าน flat `characters` schema

ก่อนแก้ต้อง trace ว่า caller ณ จุดนั้นอ่าน `story_breakdown.json`, raw v4, หรือ compatibility Context ตัวไหนจริง

### B. Storyboard transport document vs active Vision implementation

`กฏของโปรแกรม/PROMPT_REF_STORYBOARD_TRANSPORT.md` ระบุ contract ที่ Storyboard image analysis อยู่ใน Prompt-Ref conversation เดิมผ่าน captured Web transport

แต่ implementation ที่พบใน `_generate_prompts_from_storyboard_image()` ปัจจุบันส่ง Vision request แบบ temporary แล้วจึง writeback JSON แบบ text-only เข้า Prompt-Ref history เดิม

อย่าแก้ฝ่ายใดฝ่ายหนึ่งจากเอกสารเพียงอย่างเดียว ต้องตรวจ runtime/Bridge contract และ tests ก่อน เพราะนี่อาจเป็น intentional compatibility workaround หรือ documentation drift

### C. `tools/snapgen_prompt_ref_browser.py`

มี direct CDP helper สำหรับอ่าน JSON ใน Prompt-Ref conversation แต่ไม่พบ caller จาก search ปัจจุบัน จึงอย่าถือว่าเป็น active path จนกว่าจะเจอ call site/runtime wiring

---

## 14. Safe debugging order สำหรับ AI/Worker

เมื่อมี bug Prompt-Ref Context ให้เปิดตามลำดับนี้:

1. `git status`
2. `git log -5 --oneline`
3. `docs/work-reports/LATEST.md`
4. เอกสารนี้
5. `snapgen_data/meta/prompt_ref_conversation.json` (local runtime only, ระวังข้อมูล account/path)
6. `snapgen_data/prompt_ref_source.txt` และ hash relationship
7. `story_breakdown.json`
8. `prompt_ref_context.json`
9. `context_master.json`
10. caller/function ที่ bug เกิดจริง
11. tests ที่เกี่ยวข้อง
12. `git diff` ก่อนแก้

ห้ามแก้ Context schema จากอาการ UI อย่างเดียวโดยไม่ดู persistence + consumer + history marker พร้อมกัน

---

## 15. Change checklist

ก่อนแก้ Prompt-Ref Context ในอนาคต:

- [ ] รู้ว่า source story ตัวไหน authoritative
- [ ] รู้ว่า function นี้ใช้ Prompt-Ref history หรือ page-specific history
- [ ] รู้ว่า Context ที่อ่านคือ v4 breakdown หรือ compatibility Context
- [ ] ไม่ใช้ keyword classification แทน semantic identity
- [ ] ไม่สร้างชื่อ entity ใหม่ถ้าบทไม่ได้ให้ชื่อ
- [ ] ไม่ทำ temporary state เป็น permanent identity
- [ ] ไม่ทำ conversation/account หลุดกลาง flow
- [ ] ไม่เลือก Storyboard image ด้วยการเดาไฟล์ล่าสุด
- [ ] preserve `matched_refs` canonical names
- [ ] run targeted tests
- [ ] inspect `git diff` เฉพาะไฟล์งาน

---

## 16. Canonical reading rule for future AI

ถ้า AI ตัวใหม่ต้องเข้าใจ Prompt-Ref Context ให้เริ่มที่เอกสารนี้ แล้วเปิด source เฉพาะ entry points ที่ระบุ

อย่าสรุประบบจาก:

- ชื่อไฟล์อย่างเดียว
- README เก่าอย่างเดียว
- backup `.bak_*`
- runtime JSON เก่าอย่างเดียว
- GitHub HEAD อย่างเดียวเมื่อ working tree มีงานค้าง

ระบบจริง ณ เครื่องผู้ใช้ = **checkout + working tree + runtime state + active call path**

---

## Final Character Visual Bible Architecture (2026-08-08)

Character Reference ใช้ **AI-generated canonical Character Visual Bible เป็น semantic source of truth เพียงจุดเดียว**

Flow:

`บทต้นฉบับ -> Prompt-Ref AI -> canonical Character Visual Bible v5 -> structural validation/targeted missing-field repair -> prompt_ref_context.json/context_master.json -> Ref lookup by character_id/exact name -> direct serialization -> Image AI`

Canonical character fields:

- `character_id`, `name`, `entity_type`, `needs_ref`
- `appearance.age/gender/body/height/skin/hair/face/eyes`
- `occupation`, `social_status`
- `wardrobe.top/bottom/footwear/outerwear/accessories/colors/materials/condition/overall_style/source/reason`
- `visual_identity`, `evidence[]`, `assumptions[]`

Architecture rules:

- AI decides character semantics and wardrobe from the story. Python must not assign clothing presets based on role/location/keywords.
- Legacy Thai/typo keys are migrated once at the load boundary and removed from the canonical object.
- A repair request contains only the missing field paths for the matching `character_id`/name. Existing non-empty values always win over repair values.
- Ref character lookup is by `character_id` or exact canonical name, never array position.
- Character Ref prompt is serialized from that one canonical object. Front / 3/4 / Full-body use the same wardrobe object.
- Animal/supernatural wardrobe may be `null`; Python must not add human clothes.
- Character Ref does not run a second semantic wardrobe/identity design pass.
- Every Character Ref request writes `snapgen_data/debug/ref_last_request.json` with the exact canonical character and final prompt used for the request. Runtime debug data is not committed.

Primary implementation:

- `snapgen_modules/snapgen_character_visual_bible.py`
- `snapgen_modules/snapgen_prompt_ref_visual_normalization.py`
- `snapgen_modules/snapgen_character_ref_request.py`
- `snapgen_modules/snapgen_context_tools.py`
- `snapgen_modules/snapgen_page_ref.py`

Compatibility facade `snapgen_modules/snapgen_character_wardrobe.py` may serialize/migrate existing wardrobe data but must not infer semantic clothing.
