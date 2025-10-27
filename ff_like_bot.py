#!/usr/bin/env python3
import os
import sys
import logging
import sqlite3
import requests
import threading
from datetime import datetime, timezone, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

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

def get_db_connection():
    return sqlite3.connect(DB_PATH, timeout=30)

def init_db():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            permit_like INTEGER DEFAULT 0,
            permit_auto INTEGER DEFAULT 0,
            like_limit INTEGER,
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
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

init_db()

# ---------------- Helpers ----------------
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user_row(telegram_id: int):
    try:
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
            row = cur.fetchone()
            conn.close()
            return row
    except Exception as e:
        logger.error(f"Error getting user row for {telegram_id}: {e}")
        return None

def ensure_user(telegram_id: int):
    try:
        if not get_user_row(telegram_id):
            with db_lock:
                conn = get_db_connection()
                cur = conn.cursor()
                now = datetime.now(timezone.utc).isoformat()
                cur.execute("""
                    INSERT INTO users (
                        telegram_id, permit_like, permit_auto, like_limit, auto_limit,
                        daily_likes_used, daily_autos_used, last_like_reset, last_auto_reset
                    ) VALUES (?, 0, 0, NULL, NULL, 0, 0, ?, ?)
                """, (telegram_id, now, now))
                conn.commit()
                conn.close()
                logger.info(f"Created new user record for {telegram_id}")
    except Exception as e:
        logger.error(f"Error ensuring user {telegram_id}: {e}")

def reset_daily_counts_if_needed(telegram_id: int):
    try:
        ensure_user(telegram_id)
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT daily_likes_used, daily_autos_used, last_like_reset, last_auto_reset FROM users WHERE telegram_id = ?", (telegram_id,))
            result = cur.fetchone()
            conn.close()
            
            if not result:
                return
                
            used_likes, used_autos, last_like_reset, last_auto_reset = result
            
            now_utc = datetime.now(timezone.utc)
            now_bd = now_utc.astimezone(TZ)
            
            # Check like reset
            if last_like_reset:
                try:
                    last_like_reset_dt = datetime.fromisoformat(last_like_reset)
                    last_like_bd = last_like_reset_dt.astimezone(TZ)
                except:
                    last_like_bd = now_bd - timedelta(days=1)
            else:
                last_like_bd = now_bd - timedelta(days=1)
                
            if last_like_bd.date() < now_bd.date():
                with db_lock:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE users SET daily_likes_used = 0, last_like_reset = ? WHERE telegram_id = ?", 
                                (now_utc.isoformat(), telegram_id))
                    conn.commit()
                    conn.close()
            
            # Check auto reset
            if last_auto_reset:
                try:
                    last_auto_reset_dt = datetime.fromisoformat(last_auto_reset)
                    last_auto_bd = last_auto_reset_dt.astimezone(TZ)
                except:
                    last_auto_bd = now_bd - timedelta(days=1)
            else:
                last_auto_bd = now_bd - timedelta(days=1)
                
            if last_auto_bd.date() < now_bd.date():
                with db_lock:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("UPDATE users SET daily_autos_used = 0, last_auto_reset = ? WHERE telegram_id = ?", 
                                (now_utc.isoformat(), telegram_id))
                    conn.commit()
                    conn.close()
    except Exception as e:
        logger.error(f"Error resetting daily counts for {telegram_id}: {e}")

def get_like_limit(telegram_id: int):
    try:
        row = get_user_row(telegram_id)
        if not row:
            return 0
        _, permit_like, _, like_limit, _, _, _, _, _ = row
        if not permit_like:
            return 0
        return like_limit if like_limit is not None else DEFAULT_DAILY_LIKE_LIMIT
    except Exception as e:
        logger.error(f"Error getting like limit for {telegram_id}: {e}")
        return 0

def get_auto_limit(telegram_id: int):
    try:
        row = get_user_row(telegram_id)
        if not row:
            return 0
        _, _, permit_auto, _, auto_limit, _, _, _, _ = row
        if not permit_auto:
            return 0
        return auto_limit if auto_limit is not None else DEFAULT_AUTO_LIMIT
    except Exception as e:
        logger.error(f"Error getting auto limit for {telegram_id}: {e}")
        return 0

def can_send_like(telegram_id: int):
    try:
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
    except Exception as e:
        logger.error(f"Error checking like permission for {telegram_id}: {e}")
        return False, "Internal error occurred"

def can_create_auto(telegram_id: int):
    try:
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
    except Exception as e:
        logger.error(f"Error checking auto permission for {telegram_id}: {e}")
        return False, "Internal error occurred"

def record_like_use(telegram_id: int):
    try:
        reset_daily_counts_if_needed(telegram_id)
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET daily_likes_used = daily_likes_used + 1 WHERE telegram_id = ?", (telegram_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error recording like use for {telegram_id}: {e}")

def record_auto_use(telegram_id: int):
    try:
        reset_daily_counts_if_needed(telegram_id)
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET daily_autos_used = daily_autos_used + 1 WHERE telegram_id = ?", (telegram_id,))
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error(f"Error recording auto use for {telegram_id}: {e}")

def log_event(level, message):
    try:
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO logs (ts, level, message) VALUES (?, ?, ?)", 
                       (datetime.now(timezone.utc).isoformat(), level, message))
            conn.commit()
            conn.close()
        logger.info(f"{level}: {message}")
    except Exception as e:
        logger.error(f"Error logging event: {e}")

# ---------------- API call ----------------
def call_like_api(uid: str):
    url = LIKE_API_TEMPLATE.format(uid=uid)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return True, resp.text
    except requests.exceptions.RequestException as e:
        return False, f"API request failed: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"

# ---------------- Bot Command Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Free Fire Auto Like Bot ready. Use /help to see commands.")
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = (
            "*Free Fire Auto Like Bot*\n\n"
            "/like <uid> - Send like (requires permission)\n"
            "Example: /like 1234567890\n\n"
            "/auto <uid> <days> - Set up auto like (requires permission)\n"
            "Example: /auto 8385763215 30\n\n"
            "/myautos - View your active auto like tasks\n\n"
            "/removeauto <uid> - Remove an auto like task\n\n"
            "/status - Check bot status\n\n"
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
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in help command: {e}")

async def like_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        ensure_user(user.id)
        args = context.args
        if len(args) != 1:
            await update.message.reply_text("Usage: /like <uid>")
            return
        
        uid = args[0].strip()
        allowed, reason = can_send_like(user.id)
        if not allowed and not is_admin(user.id):
            await update.message.reply_text(f"❌ Cannot send like: {reason}")
            return
        
        success, resp = call_like_api(uid)
        if success:
            record_like_use(user.id)
            await update.message.reply_text(f"✅ Like sent to UID `{uid}`\nResponse: {resp}", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(f"❌ Failed to send like to UID `{uid}`\nError: {resp}", parse_mode=ParseMode.MARKDOWN)
            log_event("ERROR", f"/like failed by {user.id} for uid={uid}: {resp}")
    except Exception as e:
        logger.error(f"Error in like command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        ensure_user(user.id)
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /auto <uid> <days>")
            return
        
        uid = args[0].strip()
        try:
            days = int(args[1])
            if days <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Days must be a positive integer.")
            return
        
        # Check if user can create auto task
        allowed, reason = can_create_auto(user.id)
        if not allowed and not is_admin(user.id):
            await update.message.reply_text(f"❌ Cannot create auto task: {reason}")
            return
        
        owner_id = user.id
        # If admin is replying to a user, set owner to that user
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            owner_id = update.message.reply_to_message.from_user.id
            ensure_user(owner_id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO autos (uid, owner_id, days_left, created_at) 
                VALUES (?, ?, ?, ?)
            """, (uid, owner_id, days, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            conn.close()
        
        record_auto_use(owner_id)
        await update.message.reply_text(
            f"✅ Auto-like task created:\n"
            f"UID: `{uid}`\n"
            f"Days: {days}\n"
            f"Owner: {owner_id}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Error in auto command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def myautos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        ensure_user(user.id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT uid, days_left, created_at, last_run, last_error 
                FROM autos 
                WHERE owner_id = ? AND days_left > 0
            """, (user.id,))
            rows = cur.fetchall()
            conn.close()
        
        if not rows:
            await update.message.reply_text("No active auto-like tasks found for you.")
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
        
        await update.message.reply_text("\n\n".join(msg_lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error in myautos command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def removeauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        if len(args) != 1:
            await update.message.reply_text("Usage: /removeauto <uid>")
            return
        
        uid = args[0].strip()
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            if is_admin(user.id):
                cur.execute("DELETE FROM autos WHERE uid = ?", (uid,))
            else:
                cur.execute("DELETE FROM autos WHERE uid = ? AND owner_id = ?", (uid, user.id))
            changed = cur.rowcount
            conn.commit()
            conn.close()
        
        if changed:
            await update.message.reply_text(f"✅ Removed {changed} auto task(s) for UID `{uid}`", parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("❌ No matching task found or you don't have permission to remove it.")
    except Exception as e:
        logger.error(f"Error in removeauto command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        status_text = (
            "🤖 Bot Status Report\n\n"
            f"✅ Bot is running\n"
            f"⏰ Current time: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🗄️ Database: Connected\n"
            f"🔧 Python version: {sys.version.split()[0]}"
        )
        
        await update.message.reply_text(status_text)
    except Exception as e:
        logger.error(f"Error in status command: {e}")

# Permission admin commands (reply based)
async def permitlike_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to the user's message to grant like permission.")
            return
        
        target = update.message.reply_to_message.from_user
        ensure_user(target.id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET permit_like = 1 WHERE telegram_id = ?", (target.id,))
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Granted like permission to {target.full_name} ({target.id}).")
    except Exception as e:
        logger.error(f"Error in permitlike command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def permitauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to the user's message to grant auto permission.")
            return
        
        target = update.message.reply_to_message.from_user
        ensure_user(target.id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET permit_auto = 1 WHERE telegram_id = ?", (target.id,))
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Granted auto permission to {target.full_name} ({target.id}).")
    except Exception as e:
        logger.error(f"Error in permitauto command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def rmlike_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to the user's message to remove like permission.")
            return
        
        target = update.message.reply_to_message.from_user
        ensure_user(target.id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET permit_like = 0 WHERE telegram_id = ?", (target.id,))
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Removed like permission from {target.full_name} ({target.id}).")
    except Exception as e:
        logger.error(f"Error in rmlike command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def rmauto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reply to the user's message to remove auto permission.")
            return
        
        target = update.message.reply_to_message.from_user
        ensure_user(target.id)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("UPDATE users SET permit_auto = 0 WHERE telegram_id = ?", (target.id,))
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Removed auto permission from {target.full_name} ({target.id}).")
    except Exception as e:
        logger.error(f"Error in rmauto command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        args = context.args
        if len(args) != 3:
            await update.message.reply_text("Usage: /setlimit <telegram_id> <like|auto> <limit>")
            return
        
        try:
            tid = int(args[0])
            typ = args[1].lower()
            limit = int(args[2])
            if limit < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Invalid arguments. ID must be integer, type must be like/auto, limit must be non-negative integer.")
            return
        
        ensure_user(tid)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            if typ == "like":
                cur.execute("UPDATE users SET like_limit = ? WHERE telegram_id = ?", (limit, tid))
            elif typ == "auto":
                cur.execute("UPDATE users SET auto_limit = ? WHERE telegram_id = ?", (limit, tid))
            else:
                await update.message.reply_text("❌ Type must be 'like' or 'auto'.")
                conn.close()
                return
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Set {typ} limit for {tid} to {limit}.")
    except Exception as e:
        logger.error(f"Error in setlimit command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def removelimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /removelimit <telegram_id> <like|auto>")
            return
        
        try:
            tid = int(args[0])
            typ = args[1].lower()
        except ValueError:
            await update.message.reply_text("❌ Invalid arguments. ID must be integer, type must be like/auto.")
            return
        
        ensure_user(tid)
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            if typ == "like":
                cur.execute("UPDATE users SET like_limit = NULL WHERE telegram_id = ?", (tid,))
            elif typ == "auto":
                cur.execute("UPDATE users SET auto_limit = NULL WHERE telegram_id = ?", (tid,))
            else:
                await update.message.reply_text("❌ Type must be 'like' or 'auto'.")
                conn.close()
                return
            conn.commit()
            conn.close()
        
        await update.message.reply_text(f"✅ Removed custom {typ} limit for {tid} (back to default).")
    except Exception as e:
        logger.error(f"Error in removelimit command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def viewlimits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT telegram_id, permit_like, permit_auto, like_limit, auto_limit 
                FROM users
            """)
            rows = cur.fetchall()
            conn.close()
        
        lines = []
        for tid, pl, pa, ll, al in rows:
            like_limit = ll if ll is not None else DEFAULT_DAILY_LIKE_LIMIT
            auto_limit = al if al is not None else DEFAULT_AUTO_LIMIT
            lines.append(
                f"ID: {tid}\n"
                f"Like perm: {'✅' if pl else '❌'} (Limit: {like_limit})\n"
                f"Auto perm: {'✅' if pa else '❌'} (Limit: {auto_limit})"
            )
        
        await update.message.reply_text("\n\n".join(lines) if lines else "No user limits found.")
    except Exception as e:
        logger.error(f"Error in viewlimits command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("❌ Only admins may use this.")
            return
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
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
            conn.close()
        
        stats_text = (
            "📊 Bot Statistics:\n\n"
            f"Active auto tasks: {active_tasks}\n"
            f"Total users: {total_users}\n"
            f"Users with like permission: {like_perm_users}\n"
            f"Users with auto permission: {auto_perm_users}\n"
            f"Recent log entries (24h): {recent_logs}"
        )
        await update.message.reply_text(stats_text)
    except Exception as e:
        logger.error(f"Error in stats command: {e}")
        await update.message.reply_text("❌ An error occurred while processing your request.")

# Generic error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.error(f"Update {update} caused error {context.error}")
        
        # Only send error message if we have a message to reply to
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text("❌ An error occurred while processing your command. Please try again later.")
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# ---------------- Main ----------------
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in environment variables")
        return
    
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_cmd))
        application.add_handler(CommandHandler("like", like_cmd))
        application.add_handler(CommandHandler("auto", auto_cmd))
        application.add_handler(CommandHandler("myautos", myautos_cmd))
        application.add_handler(CommandHandler("removeauto", removeauto_cmd))
        application.add_handler(CommandHandler("status", status_cmd))

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
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
    finally:
        logger.info("Bot stopped")

if __name__ == "__main__":
    main()
