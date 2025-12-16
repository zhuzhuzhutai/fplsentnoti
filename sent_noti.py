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

# แปลงเลขวัน (0=จันทร์ ... 6=อาทิตย์) เป็นชื่อไทย
TH_WEEKDAY_SHORT = {0: "จ.", 1: "อ.", 2: "พ.", 3: "พฤ.", 4: "ศ.", 5: "ส.", 6: "อา."}
TH_WEEKDAY_FULL =  {0: "จันทร์", 1: "อังคาร", 2: "พุธ", 3: "พฤหัสบดี", 4: "ศุกร์", 5: "เสาร์", 6: "อาทิตย์"}

def weekday_th(dt: datetime, short: bool = True) -> str:
    # dt ต้องเป็น aware datetime ในโซนเวลาที่ต้องการแสดง
    wd = dt.weekday()  # 0=Mon ... 6=Sun
    return TH_WEEKDAY_SHORT[wd] if short else TH_WEEKDAY_FULL[wd]

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

    deadline_local = deadline_utc.astimezone(tz_local)
    notify_local = notify_utc.astimezone(tz_local)

    return {
        "deadline_utc": deadline_utc,
        "notify_utc": notify_utc,
        "deadline_local": deadline_local,
        "notify_local": notify_local,
        "deadline_local_str": deadline_local.strftime("%Y-%m-%d %H:%M:%S %Z%z"),
        "notify_local_str": notify_local.strftime("%Y-%m-%d %H:%M:%S %Z%z"),
        "deadline_local_wd_short": weekday_th(deadline_local, short=True),
        "deadline_local_wd_full":  weekday_th(deadline_local, short=False),
        "notify_local_wd_short":   weekday_th(notify_local, short=True),
        "notify_local_wd_full":    weekday_th(notify_local, short=False),
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
    # กำหนด mention
    if MENTION_TARGET:
        mention = f"{MENTION_TARGET} "
    elif MENTION_EVERYONE:
        mention = "@everyone "
    else:
        mention = ""

    minutes = max(1, NOTIFY_OFFSET_SECONDS // 60)
    # เพิ่มชื่อวันไทยใน content
    content = (
        f"{mention}เตือนจัดตัว FPL! เหลือ {minutes} นาที ก่อนเดดไลน์ GW{gw} | "
        # f"แจ้งเตือน: {times['notify_local_wd_short']} {times['notify_local_str']} | "
        # f"เดดไลน์: {times['deadline_local_wd_short']} {times['deadline_local_str']}"
    )

    # เพิ่มชื่อวันใน embeds ด้วย
    embeds = [{
        "title": f"FPL Deadline GW{gw}",
        "description": "อย่าลืมยืนยันตัวจริง/กัปตัน และกด Save Team",
        "color": 0x00AAFF,
        "fields": [
            {
                "name": "แจ้งเตือน (ท้องถิ่น)",
                "value": f"{times['notify_local_wd_full']} • {times['notify_local_str']}",
                "inline": False
            },
            {
                "name": "เดดไลน์ (ท้องถิ่น)",
                "value": f"{times['deadline_local_wd_full']} • {times['deadline_local_str']}",
                "inline": False
            },
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
        notify_dt_local = datetime.fromtimestamp(notify_epoch, tz=timezone.utc).astimezone(tz_local)
        now_local = datetime.now(tz_local)
        notify_str = notify_dt_local.strftime("%Y-%m-%d %H:%M:%S %Z%z")
        print(
            f"Not in notify window. "
            f"Notify at: {weekday_th(notify_dt_local, True)} {notify_str} (local). "
            f"Now: {weekday_th(now_local, True)} {now_local.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"
        )

def mode_sleep():
    evt = get_next_event()
    gw = evt["id"]
    deadline_epoch = evt["deadline_time_epoch"]
    notify_epoch = deadline_epoch - NOTIFY_OFFSET_SECONDS
    now = int(time.time())
    wait_sec = max(0, notify_epoch - now)
    tz_local = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
    notify_dt_local = datetime.fromtimestamp(notify_epoch, tz=timezone.utc).astimezone(tz_local)
    notify_str = notify_dt_local.strftime("%Y-%m-%d %H:%M:%S %Z%z")
    print(f"Sleeping {wait_sec} seconds until notify at: {weekday_th(notify_dt_local, True)} {notify_str} (local)")
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
