# -*- coding: utf-8 -*-
"""ตัวอย่าง: ค้นหาและเช่า RTX 3090 บน Vast.ai ผ่าน API key โดยตรง"""
import json
import time
import requests

API_KEY = "YOUR_VAST_API_KEY"   # ใส่ API key จาก vast.ai/console/account
BASE = "https://console.vast.ai/api/v0"

HEADERS = {"Accept": "application/json"}
AUTH = {"api_key": API_KEY}


def find_cheapest_3090():
    """หา RTX 3090 ราคาถูกสุดที่ว่างอยู่"""
    params = {**AUTH, "q": json.dumps({
        "gpu_name":     {"in": ["RTX 3090"]},
        "num_gpus":     {"gte": 1},
        "gpu_ram":      {"gte": 24000},
        "verified":     {"eq": True},
        "rentable":     {"eq": True},
        "rented":       {"eq": False},
        "type": "ondemand",
        "limit": 20,
    })}
    r = requests.get(f"{BASE}/bundles/", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    offers = r.json().get("offers") or []
    if not offers:
        raise RuntimeError("ไม่พบ RTX 3090 ที่ว่างอยู่")
    offers.sort(key=lambda x: float(x.get("dph_total") or 999))
    best = offers[0]
    print(f"พบ: {best['gpu_name']} ${float(best['dph_total']):.3f}/ชม. | {best.get('location_id','?')}")
    return best


def rent(offer, disk_gb=60, label="SnapGen Auto"):
    """เช่า instance"""
    body = {
        **AUTH,
        "disk": disk_gb,
        "label": label,
        "image": "pytorch/pytorch:2.3.0-cuda12.1-cudnn8-devel",
        "runtype": "ssh_direct",
    }
    r = requests.put(f"{BASE}/asks/{int(offer['id'])}/", json=body, headers=HEADERS, timeout=60)
    r.raise_for_status()
    instance_id = str(r.json().get("new_contract") or "")
    if not instance_id:
        raise RuntimeError("เช่าสำเร็จแต่ไม่ได้รับ instance_id")
    print(f"เช่าแล้ว instance_id={instance_id}")
    return instance_id


def wait_for_ssh(instance_id, timeout=300):
    """รอจนกว่า instance จะพร้อม"""
    print("รอ instance พร้อม", end="", flush=True)
    started = time.time()
    while time.time() - started < timeout:
        r = requests.get(f"{BASE}/instances/{instance_id}/", params=AUTH, headers=HEADERS, timeout=15)
        data = r.json().get("instances") or []
        inst = next((x for x in data if str(x.get("id")) == instance_id), None)
        if inst and inst.get("actual_status") == "running" and inst.get("ssh_host"):
            print(" พร้อมแล้ว!")
            return inst
        print(".", end="", flush=True)
        time.sleep(8)
    raise RuntimeError("รอนานเกิน — ลองเช็ค vast.ai ด้วยตัวเอง")


def destroy(instance_id):
    """คืน instance"""
    r = requests.delete(f"{BASE}/instances/{instance_id}/", params=AUTH, headers=HEADERS, timeout=30)
    print(f"คืน instance {instance_id}: {r.status_code}")


if __name__ == "__main__":
    offer = find_cheapest_3090()
    instance_id = rent(offer)
    inst = wait_for_ssh(instance_id)
    print(f"\nSSH: ssh root@{inst['ssh_host']} -p {inst['ssh_port']}")
    print(f"GPU: {inst.get('gpu_name')} x{inst.get('num_gpus')}")
    print(f"ราคา: ${float(inst.get('dph_total',0)):.3f}/ชม.")
    print("\n[กด Enter เพื่อ destroy instance และหยุดเสียตังค์]")
    input()
    destroy(instance_id)
