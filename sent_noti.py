#!/usr/bin/env python3
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()  # โหลด .env ถ้ามี
except Exception:
    pass

# ตั้งค่า
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TIMEZONE_OFFSET_HOURS = int(os.getenv("TIMEZONE_OFFSET_HOURS", "7"))  # ค่าเริ่มต้น TH = UTC+7
NOTIFY_OFFSET_SECONDS = int(os.getenv("NOTIFY_OFFSET_SECONDS", str(60 * 60)))  # แจ้งก่อนเดดไลน์กี่วินาที (เริ่มต้น 1 ชม)
WINDOW_MINUTES = int(os.getenv("WINDOW_MINUTES", "5"))  # สำหรับโหมด window: หน้าต่างเวลาที่อนุญาตให้ส่ง (นาที)
MENTION_EVERYONE = os.getenv("MENTION_EVERYONE", "true").lower() == "true"  # ใส่ @everyone หรือไม่
MENTION_TARGET = os.getenv("MENTION_TARGET", "").strip()  # ตัวอย่าง: "<@123456789012345678>" หรือ "<@&987654321098765432>"

def get_next_event() -> Dict:
    url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    evt = next((e for e in data["events"] if e.get("is_next")), None)
    if not evt:
        raise RuntimeError("No next gameweek found")
    return evt

def format_times(deadline_epoch: int):
    tz_local = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
    deadline_utc = datetime.fromtimestamp(deadline_epoch, tz=timezone.utc)
    notify_utc = deadline_utc - timedelta(seconds=NOTIFY_OFFSET_SECONDS)
    return {
        "deadline_utc": deadline_utc,
        "notify_utc": notify_utc,
        "deadline_local_str": deadline_utc.astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S %Z%z"),
        "notify_local_str": notify_utc.astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S %Z%z"),
    }

def send_discord(content: str, embeds: Optional[List[Dict]] = None):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL (set it in .env)")
    payload: Dict = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    # จัดการ rate limit แบบง่าย หากโดน 429
    if resp.status_code == 429:
        retry = int(resp.headers.get("Retry-After", "1"))
        time.sleep(retry)
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    resp.raise_for_status()
    return resp.status_code

def build_message(gw: int, times: Dict):
    mention = f"{MENTION_TARGET} " if MENTION_TARGET else ""
    content = f"{mention}เตือนจัดตัว FPL! เหลือ {NOTIFY_OFFSET_SECONDS // 60} นาที ก่อนเดดไลน์ GW{gw}"
    embeds = [{
        "title": f"FPL Deadline GW{gw}",
        "description": "อย่าลืมยืนยันตัวจริง/กัปตัน และกด Save Team",
        "color": 0x00AAFF,
        "fields": [
            {"name": "แจ้งเตือน (ท้องถิ่น)", "value": times['notify_local_str'], "inline": False},
            {"name": "เดดไลน์ (ท้องถิ่น)", "value": times['deadline_local_str'], "inline": False},
        ],
        "footer": {"text": "แหล่งข้อมูล: FPL public endpoints"},
    }]
    return content, embeds

def mode_send_now():
    evt = get_next_event()
    gw = evt["id"]
    times = format_times(evt["deadline_time_epoch"])
    content, embeds = build_message(gw, times)
    send_discord(content, embeds)
    print("Sent (send-now).")

def mode_window():
    evt = get_next_event()
    gw = evt["id"]
    deadline_epoch = evt["deadline_time_epoch"]
    notify_epoch = deadline_epoch - NOTIFY_OFFSET_SECONDS
    now = int(time.time())
    window = WINDOW_MINUTES * 60

    if notify_epoch <= now < notify_epoch + window:
        times = format_times(deadline_epoch)
        content, embeds = build_message(gw, times)
        send_discord(content, embeds)
        print("Sent within window.")
    else:
        # แสดงสถานะเพื่อ debug
        tz_local = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
        notify_str = datetime.fromtimestamp(notify_epoch, tz=timezone.utc).astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S %Z%z")
        print(f"Not in notify window. Notify at: {notify_str} (local). Now: {datetime.now(tz_local).strftime('%Y-%m-%d %H:%M:%S %Z%z')}")

def mode_sleep():
    evt = get_next_event()
    gw = evt["id"]
    deadline_epoch = evt["deadline_time_epoch"]
    notify_epoch = deadline_epoch - NOTIFY_OFFSET_SECONDS
    now = int(time.time())
    wait_sec = max(0, notify_epoch - now)
    tz_local = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
    notify_str = datetime.fromtimestamp(notify_epoch, tz=timezone.utc).astimezone(tz_local).strftime("%Y-%m-%d %H:%M:%S %Z%z")
    print(f"Sleeping {wait_sec} seconds until notify at: {notify_str} (local)")
    time.sleep(wait_sec)
    times = format_times(deadline_epoch)
    content, embeds = build_message(gw, times)
    send_discord(content, embeds)
    print("Sent after sleep.")

def main():
    """
    เลือกโหมดผ่าน ENV MODE:
    - send-now  : ส่งทันที (ทดสอบ)
    - window    : ส่งครั้งเดียวเมื่อเข้า 'หน้าต่าง' เวลา (ใช้กับ cron)
    - sleep     : นอนรอจนถึงเวลาแล้วค่อยส่ง (ทดสอบ/งานชั่วคราว)
    """
    mode = os.getenv("MODE", "send-now").lower()
    if mode == "send-now":
        mode_send_now()
    elif mode == "window":
        mode_window()
    elif mode == "sleep":
        mode_sleep()
    else:
        raise RuntimeError(f"Unknown MODE: {mode}")

if __name__ == "__main__":
    main()