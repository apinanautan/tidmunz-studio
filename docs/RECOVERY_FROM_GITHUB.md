# กู้คืน SnapGen จาก GitHub

Repository หลัก:

```text
https://github.com/tidmunzsocial-lab/tidmunz-studio
```

## วิธีติดตั้งบนเครื่องใหม่

1. ติดตั้ง Git for Windows
2. ดาวน์โหลดไฟล์ `tools/restore_from_github.bat` จาก repository
3. ดับเบิลคลิกไฟล์ โปรแกรมจะ clone source ลงโฟลเดอร์ `SnapGen`
4. ระบบจะเรียก `setup_and_run.bat` เพื่อเตรียม Python และ dependencies ต่อให้อัตโนมัติ

หรือใช้คำสั่ง:

```bat
git clone https://github.com/tidmunzsocial-lab/tidmunz-studio.git SnapGen
cd SnapGen
setup_and_run.bat
```

ข้อมูลที่ต้องตั้งค่าใหม่บนเครื่องใหม่ ได้แก่ ChatGPT account/cookies, Chrome profile
และข้อมูลเฉพาะเครื่อง ข้อมูลเหล่านี้ไม่ควรอัปโหลดขึ้น GitHub

ไฟล์งานใน `export` และข้อมูลส่วนตัวใน `snapgen_data` ไม่รวมอยู่ใน source backup
หากต้องการเก็บไฟล์เหล่านั้นต้องสำรองแยกต่างหาก
