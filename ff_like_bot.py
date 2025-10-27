import logging
import sqlite3
import requests
import threading
from datetime import datetime, timedelta
import pytz
from telegram import Update, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackContext

# --- CONFIGURATION ---
# !!! ⛔️ PLEASE FILL THESE VALUES ⛔️ !!!
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # Get this from BotFather on Telegram
ADMIN_IDS = [123456789, 987654321]  # Replace with your numeric Telegram ID(s)
# !!! ⛔️ PLEASE FILL THESE VALUES ⛔️ !!!

API_URL = "https://yunus-bhai-like-ff.vercel.app/like"
API_KEY = "gst"
DB_NAME = "/data/bot_data.db"  # This file will store all user and task data
LIKE_DEFAULT_LIMIT = 3
BD_TZ = pytz.timezone("Asia/Dhaka")

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DATABASE ---

def get_db_connection():
    """Establishes a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        return None

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    conn = get_db_connection()
    if conn:
        try:
            with conn:
                conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    has_like_permission BOOLEAN DEFAULT 0,
                    has_auto_permission BOOLEAN DEFAULT 0,
                    daily_like_count INTEGER DEFAULT 0,
                    last_like_date TEXT DEFAULT ''
                )
                ''')
                conn.execute('''
                CREATE TABLE IF NOT EXISTS limits (
                    telegram_id INTEGER,
                    type TEXT,
                    limit_value INTEGER,
                    PRIMARY KEY (telegram_id, type)
                )
                ''')
                conn.execute('''
                CREATE TABLE IF NOT EXISTS auto_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_telegram_id INTEGER,
                    target_uid TEXT,
                    end_date TEXT,
                    UNIQUE(user_telegram_id, target_uid)
                )
                ''')
            logger.info("Database initialized successfully.")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
        finally:
            conn.close()

# --- HELPER FUNCTIONS ---

def is_admin(user_id):
    """Checks if a user is an admin."""
    return user_id in ADMIN_IDS

def check_permission(user_id, perm_type):
    """Checks if a user has a specific permission ('like' or 'auto')."""
    if is_admin(user_id):
        return True
    
    conn = get_db_connection()
    if not conn:
        return False
        
    try:
        with conn:
            query = f"SELECT has_{perm_type}_permission FROM users WHERE telegram_id = ?"
            user = conn.execute(query, (user_id,)).fetchone()
            if user and user[f'has_{perm_type}_permission']:
                return True
    except sqlite3.Error as e:
        logger.error(f"Error checking permission for {user_id}: {e}")
    finally:
        conn.close()
    return False

def get_limit(user_id, limit_type):
    """Gets the custom limit for a user, or returns the default."""
    conn = get_db_connection()
    if not conn:
        return LIKE_DEFAULT_LIMIT  # Fallback
        
    limit = LIKE_DEFAULT_LIMIT
    try:
        with conn:
            custom_limit = conn.execute(
                "SELECT limit_value FROM limits WHERE telegram_id = ? AND type = ?",
                (user_id, limit_type)
            ).fetchone()
            
            if custom_limit:
                limit = custom_limit['limit_value']
    except sqlite3.Error as e:
        logger.error(f"Error getting limit for {user_id}: {e}")
    finally:
        conn.close()
    
    return limit

def check_like_limit(user_id):
    """
    Checks if a user is within their daily like limit.
    Resets the limit if it's a new day.
    Returns (True, "Message")
    """
    conn = get_db_connection()
    if not conn:
        return (False, "Bot database error. Please try again later.")

    today_str = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    limit = get_limit(user_id, 'like')
    
    try:
        with conn:
            user = conn.execute("SELECT daily_like_count, last_like_date FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            
            if not user:
                # User not in DB, but passed permission check (must be admin)
                # Add them to DB to track limits
                conn.execute("INSERT INTO users (telegram_id) VALUES (?)", (user_id,))
                return (True, "Limit OK")

            last_like_date = user['last_like_date']
            daily_like_count = user['daily_like_count']

            if last_like_date != today_str:
                # It's a new day, reset the count
                conn.execute("UPDATE users SET daily_like_count = 0, last_like_date = ? WHERE telegram_id = ?", (today_str, user_id))
                return (True, "Limit OK")
            
            if daily_like_count >= limit:
                return (False, f"You have reached your daily like limit of {limit}.")
            
            return (True, "Limit OK")
            
    except sqlite3.Error as e:
        logger.error(f"Error checking limit for {user_id}: {e}")
        return (False, "Error checking your like limit.")
    finally:
        conn.close()

def increment_like_count(user_id):
    """Increments the user's daily like count."""
    conn = get_db_connection()
    if not conn:
        return

    today_str = datetime.now(BD_TZ).strftime('%Y-%m-%d')
    try:
        with conn:
            # Ensure date is also set, in case it's their first like of the day
            conn.execute(
                "UPDATE users SET daily_like_count = daily_like_count + 1, last_like_date = ? WHERE telegram_id = ?",
                (today_str, user_id)
            )
    except sqlite3.Error as e:
        logger.error(f"Error incrementing limit for {user_id}: {e}")
    finally:
        conn.close()

def call_like_api(uid):
    """
    Calls the Free Fire Like API.
    Returns (success, message)
    """
    params = {
        "uid": uid,
        "server_name": "bd",
        "key": API_KEY
    }
    try:
        response = requests.get(API_URL, params=params, timeout=10)
        
        if response.status_code == 200:
            try:
                data = response.json()
                # Adapt this based on the *actual* API response
                if data.get("status") == "success" or "Successfully" in data.get("message", ""):
                    return (True, data.get("message", "Like sent successfully!"))
                else:
                    return (False, data.get("message", "API returned an unknown error."))
            except requests.exceptions.JSONDecodeError:
                return (False, "API returned invalid JSON response.")
        else:
            return (False, f"API request failed with status code {response.status_code}.")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"API call error for UID {uid}: {e}")
        return (False, f"Request failed: {e}")

# --- BOT COMMANDS ---

def start(update: Update, context: CallbackContext):
    """Handles the /start command."""
    update.message.reply_text(
        "Welcome to the Free Fire Auto Like Bot!\n"
        "Type /help to see the list of commands."
    )

def help_command(update: Update, context: CallbackContext):
    """Handles the /help command."""
    help_text = """
<b>Free Fire Auto Like Bot</b>

/like <uid> - Send like (requires permission)
Example: <code>/like 1234567890</code>

/auto <uid> <days> - Set up auto like (requires permission)
Example: <code>/auto 8385763215 30</code>

/myautos - View your active auto like tasks
/removeauto <uid> - Remove an auto like task

Auto like tasks run daily at 7:00 AM Bangladesh Time (UTC+6)
"""
    
    if is_admin(update.effective_user.id):
        help_text += """
<b>Admin Commands:</b>
/permitlike - Grant like permission (reply to user)
/permitauto - Grant auto like permission (reply to user)
/rmlike - Remove like permission (reply to user)
/rmauto - Remove auto permission (reply to user)
/setlimit <telegram_id> <like|auto> <limit> - Set custom limit
/removelimit <telegram_id> <like|auto> - Remove custom limit
/viewlimits - View all custom limits
/stats - View bot statistics
/stauto - Start auto like process manually
"""
    update.message.reply_html(help_text)

def like(update: Update, context: CallbackContext):
    """Handles the /like command."""
    user_id = update.effective_user.id
    
    if not check_permission(user_id, 'like'):
        update.message.reply_text("You do not have permission to use this command.")
        return

    if not context.args:
        update.message.reply_text("Usage: /like <uid>\nExample: <code>/like 1234567890</code>", parse_mode=ParseMode.HTML)
        return
        
    uid = context.args[0]
    if not uid.isdigit():
        update.message.reply_text("Invalid UID. It should only contain numbers.")
        return

    # Check limit
    can_like, message = check_like_limit(user_id)
    if not can_like:
        update.message.reply_text(message)
        return
        
    msg = update.message.reply_text(f"Sending like to UID {uid}...")
    
    success, api_message = call_like_api(uid)
    
    if success:
        increment_like_count(user_id)
        msg.edit_text(f"✅ Success! UID: {uid}\nResponse: {api_message}")
    else:
        msg.edit_text(f"❌ Failed! UID: {uid}\nReason: {api_message}")

def auto(update: Update, context: CallbackContext):
    """Handles the /auto command."""
    user_id = update.effective_user.id
    
    if not check_permission(user_id, 'auto'):
        update.message.reply_text("You do not have permission to set up auto likes.")
        return

    if len(context.args) != 2:
        update.message.reply_text("Usage: /auto <uid> <days>\nExample: <code>/auto 1234567890 30</code>", parse_mode=ParseMode.HTML)
        return

    uid = context.args[0]
    days_str = context.args[1]

    if not uid.isdigit():
        update.message.reply_text("Invalid UID. It should only contain numbers.")
        return
        
    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError
    except ValueError:
        update.message.reply_text("Invalid days. It should be a positive number.")
        return

    end_date = datetime.now(BD_TZ) + timedelta(days=days)
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error. Please try again later.")
        return

    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO auto_tasks (user_telegram_id, target_uid, end_date) VALUES (?, ?, ?)",
                (user_id, uid, end_date_str)
            )
        update.message.reply_text(f"✅ Auto like task set for UID {uid}!\nIt will run daily until {end_date.strftime('%Y-%m-%d')}.")
    except sqlite3.Error as e:
        logger.error(f"Error setting auto task for {user_id} / {uid}: {e}")
        update.message.reply_text(f"Failed to set auto task. Database error: {e}")
    finally:
        conn.close()

def myautos(update: Update, context: CallbackContext):
    """Handles the /myautos command."""
    user_id = update.effective_user.id
    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error. Please try again later.")
        return
        
    try:
        with conn:
            tasks = conn.execute(
                "SELECT target_uid, end_date FROM auto_tasks WHERE user_telegram_id = ?",
                (user_id,)
            ).fetchall()
            
            if not tasks:
                update.message.reply_text("You have no active auto like tasks.")
                return

            now = datetime.now(BD_TZ)
            message = "<b>Your Active Auto Like Tasks:</b>\n\n"
            active_tasks = 0
            
            for task in tasks:
                end_date = datetime.strptime(task['end_date'], '%Y-%m-%d %H:%M:%S').astimezone(BD_TZ)
                if end_date > now:
                    active_tasks += 1
                    days_left = (end_date - now).days
                    message += f"<b>UID:</b> <code>{task['target_uid']}</code>\n<b>Expires:</b> {end_date.strftime('%Y-%m-%d')} ({days_left} days left)\n\n"
            
            if active_tasks == 0:
                update.message.reply_text("You have no active auto like tasks.")
            else:
                update.message.reply_html(message)

    except sqlite3.Error as e:
        logger.error(f"Error fetching myautos for {user_id}: {e}")
        update.message.reply_text(f"Error fetching tasks: {e}")
    finally:
        conn.close()

def removeauto(update: Update, context: CallbackContext):
    """Handles the /removeauto command."""
    user_id = update.effective_user.id
    
    if not context.args:
        update.message.reply_text("Usage: /removeauto <uid>\nExample: <code>/removeauto 1234567890</code>", parse_mode=ParseMode.HTML)
        return
        
    uid = context.args[0]
    if not uid.isdigit():
        update.message.reply_text("Invalid UID.")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error. Please try again later.")
        return

    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM auto_tasks WHERE user_telegram_id = ? AND target_uid = ?",
                (user_id, uid)
            )
            if cur.rowcount > 0:
                update.message.reply_text(f"✅ Auto like task for UID {uid} has been removed.")
            else:
                update.message.reply_text(f"No active auto like task found for UID {uid} under your account.")
    except sqlite3.Error as e:
        logger.error(f"Error removing auto task for {user_id} / {uid}: {e}")
        update.message.reply_text(f"Failed to remove auto task. Database error: {e}")
    finally:
        conn.close()

def stauto(update: Update, context: CallbackContext):
    """Handles the /stauto (start auto) command for admins."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return
        
    update.message.reply_text("Starting manual run of auto-like tasks...")
    # Run in a new thread to avoid blocking the bot
    threading.Thread(target=run_auto_like_tasks, args=(context.bot, update.effective_user.id)).start()

# --- ADMIN COMMANDS ---

def manage_permission(update: Update, context: CallbackContext, perm_type, value):
    """Helper function to add/remove permissions."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return
        
    if not update.message.reply_to_message:
        update.message.reply_text("Please reply to a user's message to use this command.")
        return
        
    target_user = update.message.reply_to_message.from_user
    target_id = target_user.id
    target_name = target_user.first_name

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error.")
        return

    try:
        with conn:
            conn.execute(
                f"INSERT INTO users (telegram_id, has_{perm_type}_permission) VALUES (?, ?) "
                f"ON CONFLICT(telegram_id) DO UPDATE SET has_{perm_type}_permission = ?",
                (target_id, value, value)
            )
        
        action = "granted" if value else "removed"
        update.message.reply_text(f"✅ {perm_type.capitalize()} permission {action} for {target_name} (ID: {target_id}).")
        
    except sqlite3.Error as e:
        logger.error(f"Error managing permission for {target_id}: {e}")
        update.message.reply_text(f"Database error: {e}")
    finally:
        conn.close()

def permitlike(update: Update, context: CallbackContext):
    manage_permission(update, context, 'like', 1)

def permitauto(update: Update, context: CallbackContext):
    manage_permission(update, context, 'auto', 1)

def rmlike(update: Update, context: CallbackContext):
    manage_permission(update, context, 'like', 0)

def rmauto(update: Update, context: CallbackContext):
    manage_permission(update, context, 'auto', 0)

def setlimit(update: Update, context: CallbackContext):
    """Sets a custom limit for a user."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return
        
    if len(context.args) != 3:
        update.message.reply_text("Usage: /setlimit <telegram_id> <like|auto> <limit>")
        return

    try:
        target_id = int(context.args[0])
        limit_type = context.args[1].lower()
        limit_value = int(context.args[2])
        
        if limit_type not in ['like', 'auto']:
            raise ValueError("Limit type must be 'like' or 'auto'.")
        if limit_value < 0:
            raise ValueError("Limit must be a positive number.")
            
    except ValueError as e:
        update.message.reply_text(f"Invalid input: {e}\nUsage: /setlimit <telegram_id> <like|auto> <limit>")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error.")
        return

    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO limits (telegram_id, type, limit_value) VALUES (?, ?, ?)",
                (target_id, limit_type, limit_value)
            )
        update.message.reply_text(f"✅ Custom {limit_type} limit for ID {target_id} set to {limit_value}.")
    except sqlite3.Error as e:
        logger.error(f"Error setting limit for {target_id}: {e}")
        update.message.reply_text(f"Database error: {e}")
    finally:
        conn.close()

def removelimit(update: Update, context: CallbackContext):
    """Removes a custom limit for a user."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return
        
    if len(context.args) != 2:
        update.message.reply_text("Usage: /removelimit <telegram_id> <like|auto>")
        return

    try:
        target_id = int(context.args[0])
        limit_type = context.args[1].lower()
        if limit_type not in ['like', 'auto']:
            raise ValueError
    except ValueError:
        update.message.reply_text("Invalid input. Usage: /removelimit <telegram_id> <like|auto>")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error.")
        return

    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM limits WHERE telegram_id = ? AND type = ?",
                (target_id, limit_type)
            )
            if cur.rowcount > 0:
                update.message.reply_text(f"✅ Custom {limit_type} limit for ID {target_id} removed. User will revert to default.")
            else:
                update.message.reply_text(f"No custom {limit_type} limit found for ID {target_id}.")
    except sqlite3.Error as e:
        logger.error(f"Error removing limit for {target_id}: {e}")
        update.message.reply_text(f"Database error: {e}")
    finally:
        conn.close()
        
def viewlimits(update: Update, context: CallbackContext):
    """Views all custom limits."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error.")
        return

    try:
        with conn:
            limits = conn.execute("SELECT * FROM limits").fetchall()
            if not limits:
                update.message.reply_text("No custom limits are set.")
                return

            message = "<b>All Custom Limits:</b>\n\n"
            for limit in limits:
                message += f"<b>User ID:</b> <code>{limit['telegram_id']}</code>\n<b>Type:</b> {limit['type']}\n<b>Limit:</b> {limit['limit_value']}\n\n"
            update.message.reply_html(message)
    except sqlite3.Error as e:
        logger.error(f"Error viewing limits: {e}")
        update.message.reply_text(f"Database error: {e}")
    finally:
        conn.close()

def stats(update: Update, context: CallbackContext):
    """Shows bot statistics."""
    if not is_admin(update.effective_user.id):
        update.message.reply_text("This is an admin-only command.")
        return

    conn = get_db_connection()
    if not conn:
        update.message.reply_text("Bot database error.")
        return
        
    try:
        with conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            like_users = conn.execute("SELECT COUNT(*) FROM users WHERE has_like_permission = 1").fetchone()[0]
            auto_users = conn.execute("SELECT COUNT(*) FROM users WHERE has_auto_permission = 1").fetchone()[0]
            active_tasks = conn.execute("SELECT COUNT(*) FROM auto_tasks").fetchone()[0] # Note: This includes expired, we should filter
            
            # More accurate task count
            now_str = datetime.now(BD_TZ).strftime('%Y-%m-%d %H:%M:%S')
            active_tasks_filtered = conn.execute(
                "SELECT COUNT(*) FROM auto_tasks WHERE end_date > ?", (now_str,)
            ).fetchone()[0]

            message = f"""
<b>Bot Statistics</b>
- <b>Total Users in DB:</b> {total_users}
- <b>Users with Like Perms:</b> {like_users}
- <b>Users with Auto Perms:</b> {auto_users}
- <b>Active Auto-Like Tasks:</b> {active_tasks_filtered}
            """
            update.message.reply_html(message)
            
    except sqlite3.Error as e:
        logger.error(f"Error getting stats: {e}")
        update.message.reply_text(f"Database error: {e}")
    finally:
        conn.close()

# --- SCHEDULER ---

def run_auto_like_tasks(bot: 'telegram.Bot', admin_chat_id=None):
    """Scheduled job to run all auto-like tasks."""
    logger.info("Scheduler: Running auto-like tasks...")
    conn = get_db_connection()
    if not conn:
        logger.error("Scheduler: Could not connect to DB.")
        return

    now = datetime.now(BD_TZ)
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    
    tasks_to_run = []
    tasks_to_delete = []
    
    try:
        with conn:
            # Get all tasks
            all_tasks = conn.execute("SELECT id, user_telegram_id, target_uid, end_date FROM auto_tasks").fetchall()
            
            for task in all_tasks:
                end_date = datetime.strptime(task['end_date'], '%Y-%m-%d %H:%M:%S').astimezone(BD_TZ)
                
                if end_date <= now:
                    tasks_to_delete.append(task['id'])
                else:
                    tasks_to_run.append(task)
            
            # Delete expired tasks
            if tasks_to_delete:
                conn.executemany("DELETE FROM auto_tasks WHERE id = ?", [(tid,) for tid in tasks_to_delete])
                logger.info(f"Scheduler: Cleaned up {len(tasks_to_delete)} expired tasks.")

    except sqlite3.Error as e:
        logger.error(f"Scheduler: Error reading/deleting tasks: {e}")
        if admin_chat_id:
            bot.send_message(admin_chat_id, f"Error reading tasks from DB: {e}")
    finally:
        conn.close()

    logger.info(f"Scheduler: Found {len(tasks_to_run)} active tasks to process.")
    
    success_count = 0
    fail_count = 0
    
    for task in tasks_to_run:
        uid = task['target_uid']
        user_id = task['user_telegram_id']
        
        success, message = call_like_api(uid)
        
        if success:
            success_count += 1
            logger.info(f"Scheduler: Successfully sent like to UID {uid} for user {user_id}.")
        else:
            fail_count += 1
            logger.warning(f"Scheduler: Failed to send like to UID {uid} for user {user_id}. Reason: {message}")
            try:
                # Notify the user who set the task
                bot.send_message(
                    chat_id=user_id,
                    text=f"❌ Auto-like for UID <code>{uid}</code> failed.\n<b>Reason:</b> {message}",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Scheduler: Failed to send error notification to user {user_id}: {e}")

    logger.info(f"Scheduler: Run complete. Success: {success_count}, Fail: {fail_count}")
    
    if admin_chat_id:
        bot.send_message(
            admin_chat_id,
            f"Manual auto-like run complete.\nSuccess: {success_count}, Fail: {fail_count}"
        )


# --- MAIN ---

def main():
    """Starts the bot."""
    # Check for config
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or not ADMIN_IDS:
        logger.critical("!!! BOT_TOKEN or ADMIN_IDS are not set in bot.py! Please fill them and restart. !!!")
        return

    # Initialize DB
    init_db()

    # Setup Bot
    updater = Updater(BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Setup Scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone=BD_TZ)
    # Schedule the job to run daily at 7:00 AM Bangladesh time
    scheduler.add_job(
        run_auto_like_tasks,
        'cron',
        hour=7,
        minute=0,
        args=[updater.bot] # Pass the bot object to the job
    )
    scheduler.start()
    logger.info("Scheduler started. Tasks will run daily at 7:00 AM (Asia/Dhaka).")

    # Register Handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("like", like))
    dispatcher.add_handler(CommandHandler("auto", auto))
    dispatcher.add_handler(CommandHandler("myautos", myautos))
    dispatcher.add_handler(CommandHandler("removeauto", removeauto))
    dispatcher.add_handler(CommandHandler("stauto", stauto))
    
    # Admin Handlers
    dispatcher.add_handler(CommandHandler("permitlike", permitlike))
    dispatcher.add_handler(CommandHandler("permitauto", permitauto))
    dispatcher.add_handler(CommandHandler("rmlike", rmlike))
    dispatcher.add_handler(CommandHandler("rmauto", rmauto))
    dispatcher.add_handler(CommandHandler("setlimit", setlimit))
    dispatcher.add_handler(CommandHandler("removelimit", removelimit))
    dispatcher.add_handler(CommandHandler("viewlimits", viewlimits))
    dispatcher.add_handler(CommandHandler("stats", stats))

    # Start the Bot
    updater.start_polling()
    logger.info("Bot is polling...")
    updater.idle()

if __name__ == '__main__':
    main()

