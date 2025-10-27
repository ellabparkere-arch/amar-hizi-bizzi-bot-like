import os
import logging
import sqlite3
import json
from datetime import datetime, time
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from zoneinfo import ZoneInfo  # stdlib

from aiohttp import web
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

# ----------------------------
# Config / ENV
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "REPLACE_ME")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
LIKE_API_BASE = "https://yunus-bhai-like-ff.vercel.app/like"
LIKE_API_KEY = os.getenv("LIKE_API_KEY", "gst")
SERVER_NAME = os.getenv("SERVER_NAME", "bd")

DB_PATH = os.getenv("DB_PATH", "data.db")
TZ = ZoneInfo("Asia/Dhaka")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")  # e.g. https://your-service.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")
PORT = int(os.getenv("PORT", "10000"))
HOST = "0.0.0.0"
WEBHOOK_PATH = f"/{BOT_TOKEN}"
HEALTH_PATH = "/healthz"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ff-like-bot")

# ----------------------------
# DB helpers
# ----------------------------
def db_connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

CON = db_connect()
with CON:
    CON.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            days_remaining INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

def upsert_task(uid: str, creator_id: int, days: int):
    with CON:
        CON.execute("""
            INSERT INTO tasks(uid, creator_id, days_remaining, created_at)
            VALUES(?,?,?,?)
            ON CONFLICT(uid) DO UPDATE SET
                creator_id=excluded.creator_id,
                days_remaining=excluded.days_remaining
        """, (uid, creator_id, days, datetime.now(TZ).isoformat()))
    return True

def get_tasks_for_user(creator_id: int):
    cur = CON.execute("SELECT uid, days_remaining, created_at FROM tasks WHERE creator_id = ? ORDER BY uid", (creator_id,))
    return cur.fetchall()

def get_all_tasks():
    cur = CON.execute("SELECT uid, creator_id, days_remaining FROM tasks ORDER BY uid")
    return cur.fetchall()

def remove_task(uid: str):
    with CON:
        cur = CON.execute("DELETE FROM tasks WHERE uid = ?", (uid,))
    return cur.rowcount > 0

def extend_task_days(uid: str, delta_days: int) -> Optional[int]:
    with CON:
        cur = CON.execute("SELECT days_remaining FROM tasks WHERE uid = ?", (uid,))
        row = cur.fetchone()
        if not row:
            return None
        new_days = max(0, row["days_remaining"] + delta_days)
        CON.execute("UPDATE tasks SET days_remaining = ? WHERE uid = ?", (new_days, uid))
        return new_days

# ----------------------------
# Like API via urllib (no requests dep)
# ----------------------------
def call_like_api(uid: str) -> (bool, str):
    qs = urlencode({"uid": uid, "server_name": SERVER_NAME, "key": LIKE_API_KEY})
    url = f"{LIKE_API_BASE}?{qs}"
    req = Request(url, headers={"User-Agent": "ff-like-bot/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
                success = bool(data.get("success", 200 <= status < 300))
                msg = data.get("message") or data.get("msg") or body[:200]
                return success, msg
            except json.JSONDecodeError:
                return (200 <= status < 300), (body[:200] if body else f"HTTP {status}")
    except HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        return False, f"HTTP {e.code}: {detail[:200]}"
    except URLError as e:
        return False, f"Network error: {e.reason}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

# ----------------------------
# Helpers
# ----------------------------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

HELP_TEXT = (
    "Free Fire Auto Like Bot\n\n"
    "/like <uid> — Like পাঠাবে (requires permission)\n"
    "উদাহরণ: /like 1234567890\n\n"
    "/auto <uid> <days> — প্রতিদিন সকাল ৭টা (BD Time) auto like সেট (admin only)\n"
    "উদাহরণ: /auto 8385763215 30\n\n"
    "/myautos — আপনার active auto like টাস্কগুলো দেখুন\n"
    "/removeauto <uid> — কোনো auto like টাস্ক মুছুন\n"
    "/stauto — Auto like process manual ভাবে এখনই চালু করুন (admin only)\n\n"
    "নোট: Auto like টাস্ক প্রতিদিন 7:00 AM Bangladesh Time (UTC+6) এ রান করে।\n"
    "Extra: /extendauto <uid> <
