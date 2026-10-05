# คู่มือทำตัวละคร Character Creator 5 (CC5) — อ่านไฟล์นี้ไฟล์เดียวแล้วทำได้เลย

ใช้เมื่อผู้ใช้สั่ง "ทำตัวละคร <ชื่อ>" หรือ "ทำตัวที่เหลือต่อ" ของเรื่องนิทาน/เรื่องเล่า
ทำทีละตัว จบแล้วรายงานสั้นๆ เป็นภาษาไทย พร้อมส่งรูปเรนเดอร์ 1 รูป

## 1. ที่อยู่ของทุกอย่าง

| อะไร | อยู่ที่ |
|---|---|
| สคริปต์ (ส่วนหนึ่งของโปรแกรม) | `%LOCALAPPDATA%\Tidmunz Studio\snapgen_modules\cc5\` — ซอร์สอยู่ใน repo `snapgen_modules/cc5/` |
| งานของแต่ละตัว (job) | `%LOCALAPPDATA%\Tidmunz Studio\snapgen_data\cc5_jobs\<ชื่อภาษาอังกฤษตัวเล็ก_ขีดล่าง>\` |
| ตัวชี้ job ปัจจุบัน | `snapgen_data\cc5_jobs\current_job.txt` (บรรทัดเดียว = path โฟลเดอร์ job) |
| สารบัญของ CC5 (ทุกไฟล์ในคลัง) | `%LOCALAPPDATA%\Tidmunz Studio\cc5_catalog\catalog.json` — ค้นด้วย `cc5_find.py` (ข้อ 3) |
| ของที่ลองใช้จริงแล้ว (ใช้ได้/เสีย/ไม่เอา/ใช้กับใคร) | `snapgen_data\cc5_wardrobe.json` |
| ทรงผมที่คัดด้วยตาแล้ว | `snapgen_data\cc5_hair_pool.json` |
| รายชื่อตัวละครของเรื่อง (ข้อมูลชุด) | `snapgen_data\story_face_batch_latest.json` → key `text` (ใช้เลขข้อตามนี้เสมอ เช่น 3.1, 8, 9) |
| ชื่อภาษาอังกฤษของตัวละคร | `snapgen_data\karaoke_romanizations.json` |
| รูปหน้า | `Desktop\Project snapgen.ai\export\story_face\<ชื่อไทย>_face.png` |
| คลังเสื้อผ้า/ผมของผู้ใช้ | `G:\คอมพิวเตอร์เครื่องอื่นๆ\คอมพิวเตอร์ของฉัน\โมชั่น\Prop ของใช้ต่างๆ\เสื้อผ้า-ทรงผม` |
| ที่ส่งงานจริง | โฟลเดอร์เรื่องใน Google Drive `G:\ไดรฟ์ของฉัน\<เรื่อง>\...` (ดูว่ามี `.iAvatar` ตัวก่อนๆ อยู่ที่ไหน) |

ห้ามเขียนสคริปต์หรือ job ไว้ใน `export\` (ผู้ใช้ล้างทิ้งบ่อย)

## 2. กฎของผู้ใช้ (ห้ามผิด)

- สไตล์ชาวบ้านไทย: ชุดเรียบ สีพื้น ไม่มีลาย ไม่แฟชั่น ไม่ไซไฟ ไม่เปิดเผย ไม่มีโลโก้ เว้นแต่บทบอกว่าแต่งดี
- ผมสีดำ, วัยกลางคนสีเทา, คนแก่สีขาว — ห้ามน้ำตาล/บลอนด์
- ผู้ชายห้ามใช้ทรงผมผู้หญิง (ผมยาวประบ่า มวย ฯลฯ) และห้ามใช้ทรงเดียวกันซ้ำในเรื่อง — สุ่มด้วยสคริปต์ (ข้อ 4)
- ร่างกาย: ผู้ใหญ่และคนแก่ทั่วไป = Neutral, เด็ก = Child, ทารก = Baby, Male/Female เฉพาะตัวที่บทบอกว่ากล้ามใหญ่
- ชื่อไฟล์เป็นภาษาอังกฤษตาม karaoke เท่านั้น (เช่น `Wanna Child`)
- ส่งเข้าโฟลเดอร์เรื่องแค่ไฟล์ `.iAvatar` ไฟล์เดียว (png/ccProject เก็บใน job)
- ห้ามใช้ Reallusion Hub, ห้ามติ๊ก Generate Hair, ห้ามเขียนทับงานที่ผู้ใช้ยังไม่ได้เซฟ (ชื่อหน้าต่าง CC5 ลงท้าย `*` = ถามก่อน)
- ห้ามแก้โค้ดโปรแกรมเพื่อตัวละครตัวเดียว

## 3. ประหยัด token (สำคัญ)

- เลือกของจาก **ข้อความ** ไม่ใช่รูป:
  `python "%LOCALAPPDATA%\Tidmunz Studio\snapgen_modules\cc5\cc5_find.py" cloth หญิง หนุ่มสาว`
  (ใส่ `--all` เพื่อดูรายการที่ยังไม่ได้ตรวจด้วย; รายการ broken/rejected ถูกซ่อนให้แล้ว)
- เลือกจาก `cc5_wardrobe.json` สถานะ `ok` ก่อน เพราะลองแล้วว่าใช้ได้
- ระหว่างรอ CC5 ห้ามถ่ายจอ — อ่าน `result.txt` ใน job แทน ถ่ายจอเล็ก (scale 0.3) เฉพาะตอนมีอะไรผิด
- ดูรูปเรนเดอร์ครั้งเดียวตอนจบ (ย่อก่อน)

## 4. ขั้นตอนทำ 1 ตัว

1. **รายชื่อ**: อ่านข้อมูลชุด เทียบกับ `.iAvatar` ที่มีในโฟลเดอร์เรื่อง เพื่อรู้ว่าตัวไหนยังไม่ทำ (รายงานเลขข้อตามข้อมูลชุด)
2. **หน้า**: ใช้ `<ชื่อ>_face.png` ถ้ามี ไม่มีก็สร้างด้วย
   `snapgen_image_gen.generate_image(prompt, output_dir=...story_face, name_hint="<ชื่อ>_face", aspect_ratio="1:1", temporary_chat=True)`
   prompt: คนไทย อายุตรงตัว หน้าตรงแบบรูปติดบัตร ปากปิด พื้นเทาอ่อน เห็นหู ผมไม่ปิดหน้าผาก (เด็ก/หนุ่มสาวต้องไม่มีริ้วรอย) ดูรูปย่อ 1 ครั้งก่อนใช้
3. **job**: สร้างโฟลเดอร์ job, คัดลอก `face.png` และไฟล์ชุด (ตั้งชื่อ ascii เช่น `shirt.ccCloth`, `pants.ccCloth`, `outfit.ccCloth`, `sandals.ccShoes`) แล้วเขียน `job.json`:
   ```json
   {"face": "face.png", "body": "neutral", "skip_headshot": true,
    "items": ["pants.ccCloth", "shirt.ccCloth", "sandals.ccShoes"], "name": "Wanna Young"}
   ```
   - ใส่กางเกงก่อนเสื้อ (ลดเสื้อทะลุกางเกง) หรือใช้ชุดชิ้นเดียว (`full`) ถ้ามี
   - ใช้ได้เฉพาะ `.rlHair` — `.ccHair` โหลดไม่ขึ้น, หลีกเลี่ยง `.iCloth`
4. **ผม (สุ่ม)**: `python "%LOCALAPPDATA%\Tidmunz Studio\snapgen_modules\cc5\cc5_pick_hair.py" <job> <male|female> <child|adult|middle|old>`
   สคริปต์ใส่ `hair.rlHair`, ตั้งสีตามวัย และข้ามทรงที่ตัวอื่นใช้แล้ว
5. เขียน path ของ job ลง `current_job.txt`
6. **หัว (ใน CC5, ต้องใช้ computer-use กับแอป "Character Creator v5.07")** — หน้าต่าง CC5 ขยายเต็มจอ, พิกัดในเฟรม 1456x819:
   - แท็บ Headshot 3 ด้านซ้าย → Regenerate Character (198,538)
   - ไอคอนโฟลเดอร์ Head Photo (562,202) → ช่องชื่อไฟล์ (600,714) พิมพ์ path ของ `face.png` → Enter
   - แท็บ BODY (864,166) → เลือกร่าง: Neutral (566,252) / Child (774,252)
   - GENERATE (725,620) → รอ ~4 นาที (ใช้ wait ไม่ต้องถ่ายจอ) → มี popup "Failed to record undo" กด OK ได้
7. **แต่งตัว**: Script (400,30) → Load Python (442,53) → รอ ~10 วินาทีให้หน้าต่างเปิด → ช่องชื่อไฟล์ (600,711) พิมพ์
   `C:\Users\Apinan\AppData\Local\Tidmunz Studio\snapgen_modules\cc5\cc5_build_character.py` → Enter → รอ ~90 วินาที
   ทุกบรรทัดใน `result.txt` ต้องขึ้นต้นด้วย OK (ถ้าขึ้นหน้าต่าง Missing texture กด OK ได้)
   รันซ้ำได้: สคริปต์ถอดของเก่าแล้วใส่ใหม่ทั้งหมด (ใช้เปลี่ยนผม/ชุด)
8. **ตรวจ**: ดู `<name>.png` ใน job (ย่อแล้ว) — หน้าเหมือน, ผมถูกสี/ถูกเพศ, เห็นรองเท้า, เสื้อไม่ทะลุ
9. **Export**: File (13,30) → Export (62,200) → iAvatar (318,206) → Export (654,576) → ช่องชื่อ (600,663) Ctrl+A พิมพ์ `<name>.iAvatar` → Enter
   ถ้าถามเขียนทับ กดปุ่ม "ใช่" (778,400) — เขียนทับได้เฉพาะไฟล์ที่ตัวเองทำในงานนี้
10. **บันทึก**: เพิ่ม/อัปเดตของทุกชิ้นที่ลองใน `cc5_wardrobe.json` (status ok/broken/rejected, used_by, note) แล้วส่งรูปเรนเดอร์ให้ผู้ใช้

## 5. ปัญหาที่เจอแล้ว

- มีหน้าต่างอื่นทับ CC5 (เช่น Task Manager ที่อยู่บนสุด) → ขอให้ผู้ใช้ปิด เพราะคลิกทะลุไม่ได้
- Headshot ขึ้น "Failed to detect facial landmarks" → เช็ก `"C:\Program Files\Reallusion\Character Creator 5\Bin64\CharacterCreatorpy.exe" -c "import cv2"` ถ้าพังให้รีสตาร์ทเครื่อง
- ผมโหลดแล้วไม่ขึ้น (0.0s) = ไฟล์ `.ccHair` → เปลี่ยนเป็น `.rlHair`
- `hair.Update()` ไม่รับอาร์กิวเมนต์ใน CC5
