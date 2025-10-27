import os
import logging
import sqlite3
import requests
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)

# ----------------------------
# Config / ENV
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "REPLACE_ME")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}  # e.g. "123,456"
LIKE_API_BASE = "https://yunus-bhai-like-ff.vercel.app/like"
LIKE_API_KEY = os.getenv("LIKE_API_KEY", "gst")
SERVER_NAME = os.getenv("SERVER_NAME", "bd")

DB_PATH = os.getenv("DB_PATH", "data.db")
TZ = timezone("Asia/Dhaka")

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
# Like API
# ----------------------------
def call_like_api(uid: str) -> (bool, str):
    """Returns (ok, message)."""
    try:
        r = requests.get(
            LIKE_API_BASE,
            params={"uid": uid, "server_name": SERVER_NAME, "key": LIKE_API_KEY},
            timeout=20
        )
        # Try to parse JSON; fallback to text
        try:
            data = r.json()
            # Common fields guess:
            success = bool(data.get("success", r.ok))
            msg = data.get("message") or data.get("msg") or str(data)
            return success, msg
        except Exception:
            # Not JSON
            if r.ok:
                return True, f"OK: {r.text[:200]}"
            else:
                return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except requests.RequestException as e:
        return False, f"Network error: {e}"

# ----------------------------
# Telegram helpers
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
    "Extra: /extendauto <uid> <+/-days> — টাস্কের দিন বাড়ানো/কমানো (admin only)"
)

# ----------------------------
# Command handlers
# ----------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def like_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("ব্যবহার: /like <uid>\nউদাহরণ: /like 1234567890")
        return
    uid = args[0].strip()
    ok, msg = call_like_api(uid)
    if ok:
        await update.message.reply_text(f"✅ Like sent to UID {uid}\nResponse: {msg}")
    else:
        await update.message.reply_text(f"❌ Failed for UID {uid}\nError: {msg}")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("এই কমান্ডটি শুধুমাত্র admin ব্যবহার করতে পারবে।")
        return
    args = context.args
    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        await update.message.reply_text("ব্যবহার: /auto <uid> <days>\nউদাহরণ: /auto 8385763215 30")
        return
    uid = args[0].strip()
    days = int(args[1])
    if days <= 0:
        await update.message.reply_text("days অবশ্যই 1 বা তার বেশি হতে হবে।")
        return
    upsert_task(uid, user_id, days)
    await update.message.reply_text(f"✅ Auto like সেট হয়েছে: UID {uid}, {days} দিন। প্রতিদিন সকাল 7টা (BD) রান করবে।")

async def myautos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rows = get_tasks_for_user(user_id)
    if not rows:
        await update.message.reply_text("আপনার কোনো active auto like টাস্ক নেই।")
        return
    lines = ["আপনার Auto Like টাস্ক:"]
    for r in rows:
        lines.append(f"• UID {r['uid']} — {r['days_remaining']} দিন বাকি (added: {r['created_at']})")
    await update.message.reply_text("\n".join(lines))

async def removeauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("ব্যবহার: /removeauto <uid>")
        return
    uid = args[0].strip()
    if remove_task(uid):
        await update.message.reply_text(f"🗑️ টাস্ক মুছে ফেলা হয়েছে: UID {uid}")
    else:
        await update.message.reply_text(f"কোনো টাস্ক পাওয়া যায়নি UID {uid} এর জন্য।")

async def stauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("এই কমান্ডটি শুধুমাত্র admin ব্যবহার করতে পারবে।")
        return
    count_ok, count_fail = await run_daily_jobs(context)
    await update.message.reply_text(f"Manual auto-like সম্পন্ন। ✅ {count_ok}, ❌ {count_fail}")

async def extendauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("এই কমান্ডটি শুধুমাত্র admin ব্যবহার করতে পারবে।")
        return
    args = context.args
    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        await update.message.reply_text("ব্যবহার: /extendauto <uid> <+/-days>\nউদাহরণ: /extendauto 8385763215 +7")
        return
    uid = args[0].strip()
    delta = int(args[1])
    new_days = extend_task_days(uid, delta)
    if new_days is None:
        await update.message.reply_text("এই UID-এর কোনো টাস্ক পাওয়া যায়নি।")
    else:
        await update.message.reply_text(f"✅ UID {uid} টাস্ক আপডেট হয়েছে। নতুন days_remaining: {new_days}")

# ----------------------------
# Scheduler job
# ----------------------------
async def run_daily_jobs(context: ContextTypes.DEFAULT_TYPE):
    rows = get_all_tasks()
    ok_cnt, fail_cnt = 0, 0

    for r in rows:
        uid = r["uid"]
        days = r["days_remaining"]
        if days <= 0:
            remove_task(uid)
            continue

        ok, msg = call_like_api(uid)
        if ok:
            ok_cnt += 1
            # Decrement day
            extend_task_days(uid, -1)
            # If now zero, remove
            with CON:
                cur = CON.execute("SELECT days_remaining FROM tasks WHERE uid = ?", (uid,))
                left = cur.fetchone()["days_remaining"]
            if left <= 0:
                remove_task(uid)
        else:
            fail_cnt += 1
            # Don’t decrement on failure; show reason to creator (if possible)
            try:
                await context.bot.send_message(
                    chat_id=r["creator_id"],
                    text=f"❌ Auto-like FAILED for UID {uid}\nকারণ: {msg}"
                )
            except Exception as e:
                log.warning(f"Notify failed: {e}")

    log.info(f"Daily job done: ok={ok_cnt}, fail={fail_cnt}")
    return ok_cnt, fail_cnt

def schedule_jobs(app):
    scheduler = BackgroundScheduler(timezone=TZ)
    # Run every day at 07:00 Asia/Dhaka
    scheduler.add_job(lambda: app.create_task(run_daily_jobs(app.bot)), CronTrigger(hour=7, minute=0))
    scheduler.start()
    return scheduler

# ----------------------------
# App bootstrap
# ----------------------------
def main():
    if BOT_TOKEN == "REPLACE_ME":
        raise RuntimeError("Set BOT_TOKEN env var before running.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("like", like_cmd))
    application.add_handler(CommandHandler("auto", auto_cmd))
    application.add_handler(CommandHandler("myautos", myautos_cmd))
    application.add_handler(CommandHandler("removeauto", removeauto_cmd))
    application.add_handler(CommandHandler("stauto", stauto_cmd))
    application.add_handler(CommandHandler("extendauto", extendauto_cmd))  # optional extra

    # Start scheduler after app is ready
    schedule_jobs(application)

    log.info("Bot is running (long-polling)…")
    application.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
