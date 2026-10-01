# การติดตั้ง “ติดมันส์ สตูดิโอ” บนเครื่องอื่น

ระบบตรวจและแก้บัคต้องทำงานจากสภาพจริงของ **เครื่องที่กำลังใช้งาน** เสมอ ห้ามอิงชื่อผู้ใช้ `Apinan`, ไดรฟ์ `C:` หรือโปรแกรมที่ติดตั้งอยู่เฉพาะเครื่องผู้พัฒนา

1. ดาวน์โหลด `Tidmunz Studio.exe` จากหน้า GitHub Release ล่าสุด (https://github.com/tidmunzsocial-lab/tidmunz-studio/releases/latest) แล้ววางไว้บน Desktop
2. ดับเบิลคลิกครั้งแรก โปรแกรมจะติดตั้งตัวเองไว้ที่ `%LOCALAPPDATA%\Tidmunz Studio` แล้วเปิด `setup_and_run.bat` เพื่อเตรียม Python 3.12 ให้อัตโนมัติ ครั้งต่อไปกด exe ตัวเดิมเพื่อเปิดได้ทันที
3. อัปเดต: ในโปรแกรมเปิด Settings แล้วกดปุ่มตรวจอัปเดต ไม่ต้องดาวน์โหลด exe ใหม่
4. ในโปรแกรมเปิด Settings แล้วกด `🩺 ตรวจและแก้บัค` ระบบจะตรวจ/ติดตั้ง Python packages, FFmpeg, curl และ Tailscale ให้อัตโนมัติ
5. Bridge ดาวน์โหลดด้วย ZIP ได้ จึงไม่บังคับว่าต้องมี Git, uv หรือ winget
6. Account/cookies และ API keys เป็นข้อมูลเฉพาะผู้ใช้ แต่ละเครื่องต้องเพิ่ม Account ของตัวเองใน Bridge Manager ห้ามแจกไฟล์ตั้งค่าที่มี token/cookies ของเครื่องอื่น
7. ทุกเครื่องใช้ Bridge ภายในเครื่องตัวเองที่ `127.0.0.1:8000` และเพิ่ม ChatGPT Account ของเครื่องนั้นเอง
8. Tailscale ยังใช้บัญชีเดียวกันสำหรับตรวจสถานะเครือข่าย แต่ไม่ใช้เป็นที่อยู่ Bridge กลาง

ถ้าซ่อมไม่ครบ รายงานจะอยู่ที่ `snapgen_data/logs/system_repair.json` และจะแสดงสาเหตุจริงในหน้าต่างซ่อม

## ออกเวอร์ชันใหม่ (ผู้พัฒนา)

แก้โค้ดใน GitHub แล้ว push tag เช่น `v1.0.1` — GitHub Actions (`.github/workflows/release.yml`) จะสร้าง `tidmun-studio-patch.zip` และ `Tidmunz Studio.exe` แล้วเผยแพร่ Release ให้ทุกเครื่องกดอัปเดตได้เอง
