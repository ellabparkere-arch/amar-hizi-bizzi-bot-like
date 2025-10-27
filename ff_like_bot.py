#!/usr/bin/env python3
import os
import logging
import sqlite3
import requests
import threading
from datetime import datetime, timezone, timedelta
import pytz

# Try to import for v20.x, fallback to v13.x
try:
    # For python-telegram-bot v20.x
    from telegram import Update
    from telegram.constants import ParseMode
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
    USE_V20 = True
except ImportError:
    # For python-telegram-bot v13.x
    from telegram import Update, ParseMode
    from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters
    USE_V20 = False

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")  # comma separated telegram ids
ADMIN_IDS = set(int(x.strip()) for x in ADMIN_IDS.split(",") if x.strip())

# The Like API (as provided)
LIKE_API_TEMPLATE = "https://yunus-bhai-like-ff.vercel.app/like?uid={uid}&server_name=bd&key=gst"

# Default limits
DEFAULT_DAILY_LIKE_LIMIT = 3
DEFAULT_AUTO_LIMIT = 5

TZ = pytz.timezone("Asia/Dhaka")  # Bangladesh time (UTC+6)

# ---------------- Logging ----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------- Database ----------------
DB_PATH = os.getenv("DB_PATH", "bot.db")
db_lock = threading.Lock()

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        permit_like INTEGER DEFAULT 0,
        permit_auto INTEGER DEFAULT 0,
        like_limit INTEGER,   -- nullable: if NULL use default
        auto_limit INTEGER,
        daily_likes_used INTEGER DEFAULT 0,
        daily_autos_used INTEGER DEFAULT 0,
        last_like_reset TEXT,
        last_auto_reset TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS autos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT,
        owner_id INTEGER,
        days_left INTEGER,
        created_at TEXT,
        last_run TEXT,
        last_error TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        level TEXT,
        message TEXT
    )
    """)
    conn.commit()
    return conn

db = init_db()

# ---------------- Helpers ----------------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user_row(telegram_id: int):
    with db_lock:
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone()

def ensure_user(telegram_id: int):
    with db_lock:
        cur = db.cursor()
        if not get_user_row(telegram_id):
            now = datetime.now(timezone.utc).isoformat()
            cur.execute("""
                INSERT INTO users (
                    telegram_id, permit_like, permit_auto, like_limit, auto_limit,
                    daily_likes_used, daily_autos_used, last_like_reset, last_auto_reset
                ) VALUES (?, 0, 0, NULL, NULL, 0, 0, ?, ?)
            """, (telegram_id, now, now))
            db.commit()

def reset_daily_counts_if_needed(telegram_id: int):
    ensure_user(telegram_id)
    with db_lock:
        cur = db.cursor()
        cur.execute("SELECT daily_likes_used, daily_autos_used, last_like_reset, last_auto_reset FROM users WHERE telegram_id = ?", (telegram_id,))
        used_likes, used_autos, last_like_reset, last_auto_reset = cur.fetchone()
        
        now_utc = datetime.now(timezone.utc)
        now_bd = now_utc.astimezone(TZ)
        
        # Check like reset
        if last_like_reset:
            last_like_reset_dt = datetime.fromisoformat(last_like_reset)
            last_like_bd = last_like_reset_dt.astimezone(TZ)
        else:
            last_like_bd = now_bd - timedelta(days=1)
            
        if last_like_bd.date() < now_bd.date():
            cur.execute("UPDATE users SET daily_likes_used = 0, last_like_reset = ? WHERE telegram_id = ?", 
                        (now_utc.isoformat(), telegram_id))
        
        # Check auto reset
        if last_auto_reset:
            last_auto_reset_dt = datetime.fromisoformat(last_auto_reset)
            last_auto_bd = last_auto_reset_dt.astimezone(TZ)
        else:
            last_auto_bd = now_bd - timedelta(days=1)
            
        if last_auto_bd.date() < now_bd.date():
            cur.execute("UPDATE users SET daily_autos_used = 0, last_auto_reset = ? WHERE telegram_id = ?", 
                        (now_utc.isoformat(), telegram_id))
            
        db.commit()

def get_like_limit(telegram_id: int):
    row = get_user_row(telegram_id)
    if not row:
        return 0
    _, permit_like, _, like_limit, _, _, _, _, _ = row
    if not permit_like:
        return 0
    return like_limit if like_limit is not None else DEFAULT_DAILY_LIKE_LIMIT

def get_auto_limit(telegram_id: int):
    row = get_user_row(telegram_id)
    if not row:
        return 0
    _, _, permit_auto, _, auto_limit, _, _, _, _ = row
    if not permit_auto:
        return 0
    return auto_limit if auto_limit is not None else DEFAULT_AUTO_LIMIT

def can_send_like(telegram_id: int):
    reset_daily_counts_if_needed(telegram_id)
    row = get_user_row(telegram_id)
    if not row:
        return False, "User not found in database"
    
    _, permit_like, _, like_limit, _, daily_used, _, _, _ = row
    if not permit_like:
        return False, "Permission to like not granted"
    
    limit = like_limit if like_limit is not None else DEFAULT_DAILY_LIKE_LIMIT
    if daily_used >= limit:
        return False, f"Daily like limit reached ({daily_used}/{limit})"
    
    return True, ""

def can_create_auto(telegram_id: int):
    reset_daily_counts_if_needed(telegram_id)
    row = get_user_row(telegram_id)
    if not row:
        return False, "User not found in database"
    
    _, _, permit_auto, _, auto_limit, _, daily_autos_used, _, _ = row
    if not permit_auto:
        return False, "Permission to create auto tasks not granted"
    
    limit = auto_limit if auto_limit is not None else DEFAULT_AUTO_LIMIT
    if daily_autos_used >= limit:
        return False, f"Daily auto task limit reached ({daily_autos_used}/{limit})"
    
    return True, ""

def record_like_use(telegram_id: int):
    reset_daily_counts_if_needed(telegram_id)
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET daily_likes_used = daily_likes_used + 1 WHERE telegram_id = ?", (telegram_id,))
        db.commit()

def record_auto_use(telegram_id: int):
    reset_daily_counts_if_needed(telegram_id)
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET daily_autos_used = daily_autos_used + 1 WHERE telegram_id = ?", (telegram_id,))
        db.commit()

def log_event(level, message):
    with db_lock:
        cur = db.cursor()
        cur.execute("INSERT INTO logs (ts, level, message) VALUES (?, ?, ?)", 
                   (datetime.now(timezone.utc).isoformat(), level, message))
        db.commit()
    logger.info(f"{level}: {message}")

# ---------------- API call ----------------
def call_like_api(uid: str):
    url = LIKE_API_TEMPLATE.format(uid=uid)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return True, resp.text
    except Exception as e:
        return False, str(e)

# ---------------- Scheduler ----------------
scheduler = BackgroundScheduler(timezone=TZ)

def run_all_active_autos_once(context=None):
    """Run all autos one-by-one. Called at daily schedule AND manual /stauto"""
    with db_lock:
        cur = db.cursor()
        cur.execute("SELECT id, uid, owner_id, days_left FROM autos WHERE days_left > 0")
        rows = cur.fetchall()
    
    results = []
    for id_, uid, owner_id, days_left in rows:
        success, resp = call_like_api(uid)
        now_iso = datetime.now(timezone.utc).isoformat()
        
        with db_lock:
            cur = db.cursor()
            if success:
                cur.execute("""
                    UPDATE autos 
                    SET days_left = days_left - 1, last_run = ?, last_error = NULL 
                    WHERE id = ?
                """, (now_iso, id_))
                log_event("INFO", f"Auto like success for uid={uid} (task {id_}) owner={owner_id}")
            else:
                cur.execute("""
                    UPDATE autos 
                    SET last_run = ?, last_error = ? 
                    WHERE id = ?
                """, (now_iso, resp, id_))
                log_event("ERROR", f"Auto like FAILED for uid={uid} (task {id_}) owner={owner_id} — {resp}")
            db.commit()
        
        results.append((id_, uid, success, resp))
    return results

# Schedule daily run at 07:00 BD time
scheduler.add_job(run_all_active_autos_once, CronTrigger(hour=7, minute=0, timezone=TZ))
scheduler.start()

# ---------------- Bot Command Handlers ----------------
def start(update: Update, context):
    update.message.reply_text("Free Fire Auto Like Bot ready. Use /help to see commands.")

def help_cmd(update: Update, context):
    text = (
        "*Free Fire Auto Like Bot*\n\n"
        "/like <uid> - Send like (requires permission)\n"
        "Example: /like 1234567890\n\n"
        "/auto <uid> <days> - Set up auto like (requires permission)\n"
        "Example: /auto 8385763215 30\n\n"
        "/myautos - View your active auto like tasks\n\n"
        "/removeauto <uid> - Remove an auto like task\n\n"
        "/stauto - Start auto like process manually (admin only)\n\n"
        "Auto like tasks run daily at 7:00 AM Bangladesh Time (UTC+6)\n\n"
        "Admin Commands (use by replying to a user message where noted):\n"
        "/permitlike - Grant like permission (reply to user)\n"
        "/permitauto - Grant auto permission (reply to user)\n"
        "/rmlike - Remove like permission (reply to user)\n"
        "/rmauto - Remove auto permission (reply to user)\n"
        "/setlimit <telegram_id> <like|auto> <limit> - Set custom limit\n"
        "/removelimit <telegram_id> <like|auto> - Remove custom limit\n"
        "/viewlimits - View all custom limits\n"
        "/stats - View bot statistics\n\n"
        "Default limits:\n"
        "- Daily likes: 3 (if permitted)\n"
        "- Daily auto tasks: 5 (if permitted)\n"
    )
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def like_cmd(update: Update, context):
    user = update.effective_user
    ensure_user(user.id)
    args = context.args if USE_V20 else context.args
    if len(args) != 1:
        update.message.reply_text("Usage: /like <uid>")
        return
    
    uid = args[0].strip()
    allowed, reason = can_send_like(user.id)
    if not allowed and not is_admin(user.id):
        update.message.reply_text(f"❌ Cannot send like: {reason}")
        return
    
    success, resp = call_like_api(uid)
    if success:
        record_like_use(user.id)
        update.message.reply_text(f"✅ Like sent to UID `{uid}`\nResponse: {resp}", parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text(f"❌ Failed to send like to UID `{uid}`\nError: {resp}", parse_mode=ParseMode.MARKDOWN)
        log_event("ERROR", f"/like failed by {user.id} for uid={uid}: {resp}")

def auto_cmd(update: Update, context):
    user = update.effective_user
    ensure_user(user.id)
    args = context.args if USE_V20 else context.args
    if len(args) != 2:
        update.message.reply_text("Usage: /auto <uid> <days>")
        return
    
    uid = args[0].strip()
    try:
        days = int(args[1])
        if days <= 0:
            raise ValueError
    except ValueError:
        update.message.reply_text("❌ Days must be a positive integer.")
        return
    
    # Check if user can create auto task
    allowed, reason = can_create_auto(user.id)
    if not allowed and not is_admin(user.id):
        update.message.reply_text(f"❌ Cannot create auto task: {reason}")
        return
    
    owner_id = user.id
    # If admin is replying to a user, set owner to that user
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        owner_id = update.message.reply_to_message.from_user.id
        ensure_user(owner_id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("""
            INSERT INTO autos (uid, owner_id, days_left, created_at) 
            VALUES (?, ?, ?, ?)
        """, (uid, owner_id, days, datetime.now(timezone.utc).isoformat()))
        db.commit()
    
    record_auto_use(owner_id)
    update.message.reply_text(
        f"✅ Auto-like task created:\n"
        f"UID: `{uid}`\n"
        f"Days: {days}\n"
        f"Owner: {owner_id}",
        parse_mode=ParseMode.MARKDOWN
    )

def myautos_cmd(update: Update, context):
    user = update.effective_user
    ensure_user(user.id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("""
            SELECT uid, days_left, created_at, last_run, last_error 
            FROM autos 
            WHERE owner_id = ? AND days_left > 0
        """, (user.id,))
        rows = cur.fetchall()
    
    if not rows:
        update.message.reply_text("No active auto-like tasks found for you.")
        return
    
    msg_lines = []
    for uid, days_left, created_at, last_run, last_error in rows:
        status = "✅ Active" if days_left > 0 else "⏳ Completed"
        line = (
            f"UID: `{uid}`\n"
            f"Status: {status}\n"
            f"Days left: {days_left}\n"
            f"Created: {created_at[:10]}"
        )
        if last_run:
            line += f"\nLast run: {last_run[:10]}"
        if last_error:
            line += f"\n⚠️ Error: {last_error[:50]}..."
        msg_lines.append(line)
    
    update.message.reply_text("\n\n".join(msg_lines), parse_mode=ParseMode.MARKDOWN)

def removeauto_cmd(update: Update, context):
    user = update.effective_user
    args = context.args if USE_V20 else context.args
    if len(args) != 1:
        update.message.reply_text("Usage: /removeauto <uid>")
        return
    
    uid = args[0].strip()
    
    with db_lock:
        cur = db.cursor()
        if is_admin(user.id):
            cur.execute("DELETE FROM autos WHERE uid = ?", (uid,))
        else:
            cur.execute("DELETE FROM autos WHERE uid = ? AND owner_id = ?", (uid, user.id))
        changed = cur.rowcount
        db.commit()
    
    if changed:
        update.message.reply_text(f"✅ Removed {changed} auto task(s) for UID `{uid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text("❌ No matching task found or you don't have permission to remove it.")

def stauto_cmd(update: Update, context):
    user = update.effective_user
    if not is_admin(user.id):
        update.message.reply_text("❌ Only admins can manually start auto-run.")
        return
    
    update.message.reply_text("🔄 Starting manual auto-like run now...")
    results = run_all_active_autos_once()
    
    lines = []
    success_count = 0
    for id_, uid, ok, resp in results:
        if ok:
            lines.append(f"✅ Task {id_} (UID: {uid}) succeeded")
            success_count += 1
        else:
            lines.append(f"❌ Task {id_} (UID: {uid}) failed: {resp[:50]}...")
    
    summary = f"\n\nSummary: {success_count}/{len(results)} tasks succeeded"
    update.message.reply_text("\n".join(lines) + summary)

# Permission admin commands (reply based)
def permitlike_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("❌ Reply to the user's message to grant like permission.")
        return
    
    target = update.message.reply_to_message.from_user
    ensure_user(target.id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET permit_like = 1 WHERE telegram_id = ?", (target.id,))
        db.commit()
    
    update.message.reply_text(f"✅ Granted like permission to {target.full_name} ({target.id}).")

def permitauto_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("❌ Reply to the user's message to grant auto permission.")
        return
    
    target = update.message.reply_to_message.from_user
    ensure_user(target.id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET permit_auto = 1 WHERE telegram_id = ?", (target.id,))
        db.commit()
    
    update.message.reply_text(f"✅ Granted auto permission to {target.full_name} ({target.id}).")

def rmlike_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("❌ Reply to the user's message to remove like permission.")
        return
    
    target = update.message.reply_to_message.from_user
    ensure_user(target.id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET permit_like = 0 WHERE telegram_id = ?", (target.id,))
        db.commit()
    
    update.message.reply_text(f"✅ Removed like permission from {target.full_name} ({target.id}).")

def rmauto_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("❌ Reply to the user's message to remove auto permission.")
        return
    
    target = update.message.reply_to_message.from_user
    ensure_user(target.id)
    
    with db_lock:
        cur = db.cursor()
        cur.execute("UPDATE users SET permit_auto = 0 WHERE telegram_id = ?", (target.id,))
        db.commit()
    
    update.message.reply_text(f"✅ Removed auto permission from {target.full_name} ({target.id}).")

def setlimit_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    args = context.args if USE_V20 else context.args
    if len(args) != 3:
        update.message.reply_text("Usage: /setlimit <telegram_id> <like|auto> <limit>")
        return
    
    try:
        tid = int(args[0])
        typ = args[1].lower()
        limit = int(args[2])
        if limit < 0:
            raise ValueError
    except ValueError:
        update.message.reply_text("❌ Invalid arguments. ID must be integer, type must be like/auto, limit must be non-negative integer.")
        return
    
    ensure_user(tid)
    
    with db_lock:
        cur = db.cursor()
        if typ == "like":
            cur.execute("UPDATE users SET like_limit = ? WHERE telegram_id = ?", (limit, tid))
        elif typ == "auto":
            cur.execute("UPDATE users SET auto_limit = ? WHERE telegram_id = ?", (limit, tid))
        else:
            update.message.reply_text("❌ Type must be 'like' or 'auto'.")
            return
        db.commit()
    
    update.message.reply_text(f"✅ Set {typ} limit for {tid} to {limit}.")

def removelimit_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    args = context.args if USE_V20 else context.args
    if len(args) != 2:
        update.message.reply_text("Usage: /removelimit <telegram_id> <like|auto>")
        return
    
    try:
        tid = int(args[0])
        typ = args[1].lower()
    except ValueError:
        update.message.reply_text("❌ Invalid arguments. ID must be integer, type must be like/auto.")
        return
    
    ensure_user(tid)
    
    with db_lock:
        cur = db.cursor()
        if typ == "like":
            cur.execute("UPDATE users SET like_limit = NULL WHERE telegram_id = ?", (tid,))
        elif typ == "auto":
            cur.execute("UPDATE users SET auto_limit = NULL WHERE telegram_id = ?", (tid,))
        else:
            update.message.reply_text("❌ Type must be 'like' or 'auto'.")
            return
        db.commit()
    
    update.message.reply_text(f"✅ Removed custom {typ} limit for {tid} (back to default).")

def viewlimits_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    with db_lock:
        cur = db.cursor()
        cur.execute("""
            SELECT telegram_id, permit_like, permit_auto, like_limit, auto_limit 
            FROM users
        """)
        rows = cur.fetchall()
    
    lines = []
    for tid, pl, pa, ll, al in rows:
        like_limit = ll if ll is not None else DEFAULT_DAILY_LIKE_LIMIT
        auto_limit = al if al is not None else DEFAULT_AUTO_LIMIT
        lines.append(
            f"ID: {tid}\n"
            f"Like perm: {'✅' if pl else '❌'} (Limit: {like_limit})\n"
            f"Auto perm: {'✅' if pa else '❌'} (Limit: {auto_limit})"
        )
    
    update.message.reply_text("\n\n".join(lines) if lines else "No user limits found.")

def stats_cmd(update: Update, context):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("❌ Only admins may use this.")
        return
    
    with db_lock:
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM autos WHERE days_left > 0")
        active_tasks = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM users WHERE permit_like = 1")
        like_perm_users = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM users WHERE permit_auto = 1")
        auto_perm_users = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM logs WHERE ts > datetime('now', '-1 day')")
        recent_logs = cur.fetchone()[0]
    
    stats_text = (
        "📊 Bot Statistics:\n\n"
        f"Active auto tasks: {active_tasks}\n"
        f"Total users: {total_users}\n"
        f"Users with like permission: {like_perm_users}\n"
        f"Users with auto permission: {auto_perm_users}\n"
        f"Recent log entries (24h): {recent_logs}"
    )
    update.message.reply_text(stats_text)

# Generic error handler
def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.effective_message:
        update.effective_message.reply_text("❌ An error occurred while processing your command. Please try again later.")

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment variables")
        return
    
    if USE_V20:
        # For v20.x
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_cmd))
        application.add_handler(CommandHandler("like", like_cmd))
        application.add_handler(CommandHandler("auto", auto_cmd))
        application.add_handler(CommandHandler("myautos", myautos_cmd))
        application.add_handler(CommandHandler("removeauto", removeauto_cmd))
        application.add_handler(CommandHandler("stauto", stauto_cmd))

        # Admin permission commands
        application.add_handler(CommandHandler("permitlike", permitlike_cmd))
        application.add_handler(CommandHandler("permitauto", permitauto_cmd))
        application.add_handler(CommandHandler("rmlike", rmlike_cmd))
        application.add_handler(CommandHandler("rmauto", rmauto_cmd))
        
        # Admin limit commands
        application.add_handler(CommandHandler("setlimit", setlimit_cmd))
        application.add_handler(CommandHandler("removelimit", removelimit_cmd))
        application.add_handler(CommandHandler("viewlimits", viewlimits_cmd))
        application.add_handler(CommandHandler("stats", stats_cmd))

        # Error handler
        application.add_error_handler(error_handler)

        logger.info("Starting bot...")
        application.run_polling()
    else:
        # For v13.x
        updater = Updater(BOT_TOKEN, use_context=True)
        dp = updater.dispatcher

        # Command handlers
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("help", help_cmd))
        dp.add_handler(CommandHandler("like", like_cmd))
        dp.add_handler(CommandHandler("auto", auto_cmd))
        dp.add_handler(CommandHandler("myautos", myautos_cmd))
        dp.add_handler(CommandHandler("removeauto", removeauto_cmd))
        dp.add_handler(CommandHandler("stauto", stauto_cmd))

        # Admin permission commands
        dp.add_handler(CommandHandler("permitlike", permitlike_cmd))
        dp.add_handler(CommandHandler("permitauto", permitauto_cmd))
        dp.add_handler(CommandHandler("rmlike", rmlike_cmd))
        dp.add_handler(CommandHandler("rmauto", rmauto_cmd))
        
        # Admin limit commands
        dp.add_handler(CommandHandler("setlimit", setlimit_cmd))
        dp.add_handler(CommandHandler("removelimit", removelimit_cmd))
        dp.add_handler(CommandHandler("viewlimits", viewlimits_cmd))
        dp.add_handler(CommandHandler("stats", stats_cmd))

        # Error handler
        dp.add_error_handler(error_handler)

        logger.info("Starting bot...")
        updater.start_polling()
        
        try:
            updater.idle()
        finally:
            scheduler.shutdown()
            db.close()
            logger.info("Bot stopped")

if __name__ == "__main__":
    main()
