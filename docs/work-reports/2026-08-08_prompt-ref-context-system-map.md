# Work Report: Prompt-Ref Context System Documentation

- Task: สำรวจระบบ Prompt-Ref Context ทั้งโปรเจกต์และจัดทำเอกสารให้ AI/Worker เปิดอ่านระบบได้ง่าย โดยห้ามแก้ source code และห้ามกระทบ runtime เดิม
- Status: completed
- Summary: สำรวจ Prompt-Ref story ingestion, ChatGPT conversation state, Context schema/audit/persistence, shared Context normalization, Ref/Prop/Image consumers, Storyboard generation/split/writeback, history isolation, source invalidation, tests และเอกสาร transport จาก working tree ปัจจุบัน แล้วจัดทำ system map กลาง
- Files Changed:
  - `กฏของโปรแกรม/PROMPT_REF_CONTEXT_SYSTEM.md`
  - `docs/work-reports/2026-08-08_prompt-ref-context-system-map.md`
  - `docs/work-reports/LATEST.md`
- Important Changes:
  - เพิ่มเอกสาร navigation/architecture สำหรับ Prompt-Ref Context โดยไม่แก้ runtime code
  - ระบุ runtime data files และ conversation readiness hierarchy
  - ระบุ boundary ระหว่าง shared Context data กับ page-specific ChatGPT histories
  - ระบุ semantic rules ของ Story Breakdown / Visual Bible
  - ระบุ Storyboard split contract และ canonical matched refs
  - บันทึก observed mismatches ที่ต้องตรวจเพิ่มก่อนแก้โค้ด โดยไม่เปลี่ยน behavior
- Tests/Build:
  - งานนี้เป็น documentation-only ไม่มี source-code change
  - รัน `py -3 -m unittest tests.test_prompt_ref_single_history tests.test_storyboard_split_prompt_banks tests.test_storyboard_ref_preview`
  - ผล: FAILED — Ran 18 tests, failures=4, errors=4
  - Failure ที่ตรวจเห็นเป็น contract drift ใน working tree เดิม เช่น test คาด same-history storyboard transport แต่ active implementation ใช้ temporary Vision + text writeback; และ storyboard helper expectations บางส่วนไม่ตรง source ปัจจุบัน
  - ไม่แก้ source/test เพราะงานนี้ห้ามแก้โค้ด; บันทึกเป็น baseline สำหรับงานถัดไป
  - ตรวจ `git diff --check` และ `git diff` เฉพาะไฟล์งานก่อน commit
- Remaining Issues:
  - ไม่ได้แก้ observed mismatches เพราะ requirement ระบุห้ามแก้โค้ด
  - `กฏของโปรแกรม/PROMPT_REF_STORYBOARD_TRANSPORT.md` กับ active Vision implementation มีความต่างที่ควร runtime-verify ก่อนแก้ในงานแยก
  - v4 categorized breakdown กับ v3 compatibility Context ต้องระวัง caller แต่ไม่ได้แก้ในงานนี้
- Risks:
  - Working tree เดิมมี modified/untracked files จำนวนมากจาก session ก่อนหน้า จึงต้อง stage เฉพาะไฟล์เอกสารงานนี้
  - เอกสารอ้างอิง working tree ณ 2026-08-08 ไม่ใช่เฉพาะ Git HEAD
- Git Commit: commit containing this report (exact hash recorded in final worker response; self-referential commit hash cannot be embedded inside its own content before Git creates the commit)
- Date/Time: 2026-08-08 18:17 +07:00


