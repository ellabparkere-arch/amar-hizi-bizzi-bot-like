import logging
import requests
import schedule
import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import json
import os
from pytz import timezone

# বট টোকেন এবং অ্যাডমিন আইডি সেট করুন
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # এখানে আপনার টেলিগ্রাম বট টোকেন দিন
ADMIN_IDS = [123456789]  # এখানে আপনার টেলিগ্রাম আইডি দিন (একাধিক অ্যাডমিন হলে কমা দিয়ে আলাদা করুন)

# লগিং সেটআপ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ফাইল থেকে ডেটা লোড করার ফাংশন
def load_data():
    data = {
        "users": {},
        "auto_likes": {},
        "permissions": {
            "like": {},
            "auto": {}
        },
        "limits": {},
        "stats": {
            "total_likes": 0,
            "total_auto_likes": 0,
            "failed_likes": 0
        }
    }
    
    if os.path.exists('bot_data.json'):
        try:
            with open('bot_data.json', 'r') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    
    return data

# ডেটা ফাইলে সেভ করার ফাংশন
def save_data(data):
    try:
        with open('bot_data.json', 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

# ডেটা লোড করুন
bot_data = load_data()

# লাইক পাঠানোর ফাংশন
def send_like(uid):
    try:
        url = f"https://yunus-bhai-like-ff.vercel.app/like?uid={uid}&server_name=bd&key=gst"
        response = requests.get(url)
        if response.status_code == 200:
            result = response.json()
            if result.get('success', False):
                bot_data["stats"]["total_likes"] += 1
                save_data(bot_data)
                return True, "Like sent successfully!"
            else:
                bot_data["stats"]["failed_likes"] += 1
                save_data(bot_data)
                return False, f"Failed to send like: {result.get('message', 'Unknown error')}"
        else:
            bot_data["stats"]["failed_likes"] += 1
            save_data(bot_data)
            return False, f"API request failed with status code: {response.status_code}"
    except Exception as e:
        bot_data["stats"]["failed_likes"] += 1
        save_data(bot_data)
        return False, f"Error sending like: {str(e)}"

# /like কমান্ড হ্যান্ডলার
def like_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # চেক করুন ইউজারের লাইক পারমিশন আছে কিনা
    if str(user_id) not in bot_data["permissions"]["like"] and user_id not in ADMIN_IDS:
        update.message.reply_text("You don't have permission to use this command.")
        return
    
    # চেক করুন ইউজার আজকের লিমিট পার করেছে কিনা
    today = datetime.now().strftime("%Y-%m-%d")
    if str(user_id) not in bot_data["users"]:
        bot_data["users"][str(user_id)] = {"likes": {}, "date": today}
    
    if bot_data["users"][str(user_id)]["date"] != today:
        bot_data["users"][str(user_id)] = {"likes": {}, "date": today}
    
    if str(user_id) in bot_data["limits"] and "like" in bot_data["limits"][str(user_id)]:
        like_limit = bot_data["limits"][str(user_id)]["like"]
    else:
        like_limit = 3  # ডিফল্ট লিমিট
    
    if len(bot_data["users"][str(user_id)]["likes"]) >= like_limit:
        update.message.reply_text(f"You have reached your daily like limit of {like_limit}.")
        return
    
    # ইউআইডি প্যারামিটার চেক করুন
    if not context.args:
        update.message.reply_text("Please provide a UID.\nExample: /like 1234567890")
        return
    
    uid = context.args[0]
    
    # লাইক পাঠান
    success, message = send_like(uid)
    
    if success:
        bot_data["users"][str(user_id)]["likes"][uid] = datetime.now().strftime("%H:%M:%S")
        save_data(bot_data)
        update.message.reply_text(f"✅ {message}\nUID: {uid}")
    else:
        update.message.reply_text(f"❌ {message}")

# /auto কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def auto_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    # প্যারামিটার চেক করুন
    if len(context.args) < 2:
        update.message.reply_text("Please provide UID and number of days.\nExample: /auto 8385763215 30")
        return
    
    uid = context.args[0]
    
    try:
        days = int(context.args[1])
        if days <= 0:
            raise ValueError
    except ValueError:
        update.message.reply_text("Please provide a valid number of days (positive integer).")
        return
    
    # অটো লাইক সেট করুন
    if uid not in bot_data["auto_likes"]:
        bot_data["auto_likes"][uid] = {}
    
    # যদি কোনো ইউজারের মেসেজের রিপ্লাই দিয়ে কমান্ড দেওয়া হয়
    if update.message.reply_to_message:
        target_user_id = update.message.reply_to_message.from_user.id
        
        # ইউজারের অটো লাইক পারমিশন চেক করুন
        if str(target_user_id) not in bot_data["permissions"]["auto"] and target_user_id not in ADMIN_IDS:
            update.message.reply_text("This user doesn't have auto like permission.")
            return
        
        # ইউজারের অটো লাইক লিমিট চেক করুন
        if str(target_user_id) in bot_data["limits"] and "auto" in bot_data["limits"][str(target_user_id)]:
            auto_limit = bot_data["limits"][str(target_user_id)]["auto"]
        else:
            auto_limit = 1  # ডিফল্ট অটো লাইক লিমিট
        
        # ইউজারের বর্তমান অটো লাইক সংখ্যা চেক করুন
        user_auto_count = sum(1 for task in bot_data["auto_likes"].values() if task.get("user_id") == target_user_id)
        
        if user_auto_count >= auto_limit:
            update.message.reply_text(f"This user has reached their auto like limit of {auto_limit}.")
            return
        
        bot_data["auto_likes"][uid] = {
            "user_id": target_user_id,
            "days": days,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": None,
            "active": True
        }
        
        update.message.reply_text(f"✅ Auto like set for UID: {uid} for {days} days.\nUser: @{update.message.reply_to_message.from_user.username}")
    else:
        bot_data["auto_likes"][uid] = {
            "user_id": user_id,
            "days": days,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_run": None,
            "active": True
        }
        
        update.message.reply_text(f"✅ Auto like set for UID: {uid} for {days} days.")
    
    save_data(bot_data)

# /myautos কমান্ড হ্যান্ডলার
def myautos_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    # ইউজারের অটো লাইক টাস্ক খুঁজুন
    user_autos = []
    for uid, task in bot_data["auto_likes"].items():
        if task.get("user_id") == user_id and task.get("active", False):
            remaining_days = task["days"]
            
            # যদি টাস্কটি আগে রান করা হয়ে থাকে, তাহলে অবশিষ্ট দিন হিসাব করুন
            if task.get("last_run"):
                last_run_date = datetime.strptime(task["last_run"], "%Y-%m-%d")
                days_passed = (datetime.now() - last_run_date).days
                remaining_days = max(0, task["days"] - days_passed)
            
            user_autos.append({
                "uid": uid,
                "days": remaining_days,
                "created_at": task["created_at"]
            })
    
    if not user_autos:
        update.message.reply_text("You don't have any active auto like tasks.")
        return
    
    message = "Your active auto like tasks:\n\n"
    for auto in user_autos:
        message += f"UID: {auto['uid']}\n"
        message += f"Remaining days: {auto['days']}\n"
        message += f"Created at: {auto['created_at']}\n\n"
    
    update.message.reply_text(message)

# /removeauto কমান্ড হ্যান্ডলার
def removeauto_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if not context.args:
        update.message.reply_text("Please provide a UID.\nExample: /removeauto 1234567890")
        return
    
    uid = context.args[0]
    
    if uid not in bot_data["auto_likes"]:
        update.message.reply_text("No auto like task found for this UID.")
        return
    
    # চেক করুন ইউজার এই টাস্কের মালিক কিনা বা অ্যাডমিন কিনা
    if bot_data["auto_likes"][uid].get("user_id") != user_id and user_id not in ADMIN_IDS:
        update.message.reply_text("You don't have permission to remove this auto like task.")
        return
    
    # টাস্ক ডিলিট করুন
    del bot_data["auto_likes"][uid]
    save_data(bot_data)
    
    update.message.reply_text(f"✅ Auto like task removed for UID: {uid}")

# /stauto কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def stauto_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    update.message.reply_text("Starting auto like process manually...")
    
    # অটো লাইক প্রসেস চালু করুন
    run_auto_likes(context.bot)

# অটো লাইক প্রসেস ফাংশন
def run_auto_likes(bot):
    bd_timezone = timezone('Asia/Dhaka')
    now = datetime.now(bd_timezone)
    today = now.strftime("%Y-%m-%d")
    
    completed_tasks = []
    failed_tasks = []
    
    for uid, task in list(bot_data["auto_likes"].items()):
        if not task.get("active", False):
            continue
        
        # চেক করুন টাস্কটি আজকে ইতিমধ্যে রান করা হয়েছে কিনা
        if task.get("last_run") == today:
            continue
        
        # চেক করুন দিন শেষ হয়েছে কিনা
        if task.get("last_run"):
            last_run_date = datetime.strptime(task["last_run"], "%Y-%m-%d")
            days_passed = (datetime.now(bd_timezone) - last_run_date).days
            
            if days_passed >= task["days"]:
                # টাস্ক নিষ্ক্রিয় করুন
                task["active"] = False
                save_data(bot_data)
                continue
        
        # লাইক পাঠান
        success, message = send_like(uid)
        
        if success:
            task["last_run"] = today
            bot_data["stats"]["total_auto_likes"] += 1
            completed_tasks.append(uid)
            
            # ইউজারকে নোটিফিকেশন পাঠান
            user_id = task.get("user_id")
            if user_id:
                try:
                    bot.send_message(
                        chat_id=user_id,
                        text=f"✅ Auto like sent successfully!\nUID: {uid}\nTime: {now.strftime('%H:%M:%S')}"
                    )
                except Exception as e:
                    logger.error(f"Error sending notification to user {user_id}: {e}")
        else:
            failed_tasks.append({"uid": uid, "reason": message})
        
        save_data(bot_data)
    
    # অ্যাডমিনদের রিপোর্ট পাঠান
    if completed_tasks or failed_tasks:
        report = "Auto Like Process Report:\n\n"
        
        if completed_tasks:
            report += f"✅ Successfully sent likes to {len(completed_tasks)} UIDs:\n"
            report += ", ".join(completed_tasks) + "\n\n"
        
        if failed_tasks:
            report += f"❌ Failed to send likes to {len(failed_tasks)} UIDs:\n"
            for task in failed_tasks:
                report += f"UID: {task['uid']}, Reason: {task['reason']}\n"
        
        for admin_id in ADMIN_IDS:
            try:
                bot.send_message(chat_id=admin_id, text=report)
            except Exception as e:
                logger.error(f"Error sending report to admin {admin_id}: {e}")

# সকাল ৭টায় অটো লাইক সেট করার ফাংশন
def scheduled_auto_likes(context: CallbackContext):
    run_auto_likes(context.bot)

# /permitlike কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def permitlike_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("Please reply to a user's message to grant like permission.")
        return
    
    target_user_id = update.message.reply_to_message.from_user.id
    bot_data["permissions"]["like"][str(target_user_id)] = True
    save_data(bot_data)
    
    update.message.reply_text(f"✅ Like permission granted to user: @{update.message.reply_to_message.from_user.username}")

# /permitauto কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def permitauto_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("Please reply to a user's message to grant auto like permission.")
        return
    
    target_user_id = update.message.reply_to_message.from_user.id
    bot_data["permissions"]["auto"][str(target_user_id)] = True
    save_data(bot_data)
    
    update.message.reply_text(f"✅ Auto like permission granted to user: @{update.message.reply_to_message.from_user.username}")

# /rmlike কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def rmlike_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("Please reply to a user's message to remove like permission.")
        return
    
    target_user_id = update.message.reply_to_message.from_user.id
    
    if str(target_user_id) in bot_data["permissions"]["like"]:
        del bot_data["permissions"]["like"][str(target_user_id)]
        save_data(bot_data)
        update.message.reply_text(f"✅ Like permission removed from user: @{update.message.reply_to_message.from_user.username}")
    else:
        update.message.reply_text("This user doesn't have like permission.")

# /rmauto কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def rmauto_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if not update.message.reply_to_message:
        update.message.reply_text("Please reply to a user's message to remove auto like permission.")
        return
    
    target_user_id = update.message.reply_to_message.from_user.id
    
    if str(target_user_id) in bot_data["permissions"]["auto"]:
        del bot_data["permissions"]["auto"][str(target_user_id)]
        save_data(bot_data)
        update.message.reply_text(f"✅ Auto like permission removed from user: @{update.message.reply_to_message.from_user.username}")
    else:
        update.message.reply_text("This user doesn't have auto like permission.")

# /setlimit কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def setlimit_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if len(context.args) < 3:
        update.message.reply_text("Please provide telegram_id, type (like/auto), and limit.\nExample: /setlimit 123456789 like 5")
        return
    
    try:
        target_user_id = int(context.args[0])
        limit_type = context.args[1].lower()
        limit = int(context.args[2])
        
        if limit_type not in ["like", "auto"]:
            update.message.reply_text("Type must be either 'like' or 'auto'.")
            return
        
        if limit <= 0:
            update.message.reply_text("Limit must be a positive integer.")
            return
        
        if str(target_user_id) not in bot_data["limits"]:
            bot_data["limits"][str(target_user_id)] = {}
        
        bot_data["limits"][str(target_user_id)][limit_type] = limit
        save_data(bot_data)
        
        update.message.reply_text(f"✅ {limit_type.capitalize()} limit set to {limit} for user ID: {target_user_id}")
    except ValueError:
        update.message.reply_text("Please provide valid telegram_id and limit (positive integers).")

# /removelimit কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def removelimit_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if len(context.args) < 2:
        update.message.reply_text("Please provide telegram_id and type (like/auto).\nExample: /removelimit 123456789 like")
        return
    
    try:
        target_user_id = int(context.args[0])
        limit_type = context.args[1].lower()
        
        if limit_type not in ["like", "auto"]:
            update.message.reply_text("Type must be either 'like' or 'auto'.")
            return
        
        if str(target_user_id) in bot_data["limits"] and limit_type in bot_data["limits"][str(target_user_id)]:
            del bot_data["limits"][str(target_user_id)][limit_type]
            
            # যদি ইউজারের আর কোনো লিমিট না থাকে, তাহলে ইউজারকে লিমিট ডিকশনারি থেকে সরান
            if not bot_data["limits"][str(target_user_id)]:
                del bot_data["limits"][str(target_user_id)]
            
            save_data(bot_data)
            update.message.reply_text(f"✅ {limit_type.capitalize()} limit removed for user ID: {target_user_id}")
        else:
            update.message.reply_text(f"No {limit_type} limit found for user ID: {target_user_id}")
    except ValueError:
        update.message.reply_text("Please provide a valid telegram_id (positive integer).")

# /viewlimits কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def viewlimits_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    if not bot_data["limits"]:
        update.message.reply_text("No custom limits set.")
        return
    
    message = "Custom Limits:\n\n"
    for user_id_str, limits in bot_data["limits"].items():
        message += f"User ID: {user_id_str}\n"
        if "like" in limits:
            message += f"  Like limit: {limits['like']}\n"
        if "auto" in limits:
            message += f"  Auto like limit: {limits['auto']}\n"
        message += "\n"
    
    update.message.reply_text(message)

# /stats কমান্ড হ্যান্ডলার (শুধুমাত্র অ্যাডমিন)
def stats_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        update.message.reply_text("This command is for admins only.")
        return
    
    stats = bot_data["stats"]
    active_auto_tasks = sum(1 for task in bot_data["auto_likes"].values() if task.get("active", False))
    
    message = "Bot Statistics:\n\n"
    message += f"Total likes sent: {stats['total_likes']}\n"
    message += f"Total auto likes sent: {stats['total_auto_likes']}\n"
    message += f"Failed likes: {stats['failed_likes']}\n"
    message += f"Active auto like tasks: {active_auto_tasks}\n"
    message += f"Users with like permission: {len(bot_data['permissions']['like'])}\n"
    message += f"Users with auto like permission: {len(bot_data['permissions']['auto'])}"
    
    update.message.reply_text(message)

# /help কমান্ড হ্যান্ডলার
def help_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    help_text = """
Free Fire Auto Like Bot

/like <uid> - Send like (requires permission)
Example: /like 1234567890
/auto <uid> <days> - Set up auto like (admin only)
Example: /auto 8385763215 30
/myautos - View your active auto like tasks
/removeauto <uid> - Remove an auto like task
/stauto - Start auto like process manually (admin only)

Auto like tasks run daily at 7:00 AM Bangladesh Time (UTC+6)

Note: If an admin sets auto like by replying to a user's message,
that user's like limit will be reduced by 1.
"""
    
    if user_id in ADMIN_IDS:
        help_text += """
Admin Commands:
/permitlike - Grant like permission (reply to user)
/permitauto - Grant auto like permission (reply to user)
/rmlike - Remove like permission (reply to user)
/rmauto - Remove auto permission (reply to user)
/setlimit <telegram_id> <like|auto> <limit> - Set custom limit
/removelimit <telegram_id> <like|auto> - Remove custom limit
/viewlimits - View all custom limits
/stats - View bot statistics

➤ Default like limit: 3/day (if permitted)
➤ Auto like tasks run daily at 7 AM Bangladesh Time
"""
    
    update.message.reply_text(help_text)

# স্টার্ট কমান্ড হ্যান্ডলার
def start_command(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    
    welcome_text = f"""
Welcome to Free Fire Auto Like Bot, {update.effective_user.first_name}!

Use /help to see all available commands.

Note: You need permission to use this bot. Contact an admin for access.
"""
    
    update.message.reply_text(welcome_text)

# ইরর হ্যান্ডলার
def error_handler(update: Update, context: CallbackContext):
    logger.error(f"Update {update} caused error {context.error}")
    
    # অ্যাডমিনদের ইরর নোটিফিকেশন পাঠান
    for admin_id in ADMIN_IDS:
        try:
            context.bot.send_message(
                chat_id=admin_id,
                text=f"⚠️ Bot Error:\n\nUpdate: {update}\nError: {context.error}"
            )
        except Exception as e:
            logger.error(f"Error sending error notification to admin {admin_id}: {e}")

# মেইন ফাংশন
def main():
    # আপডেটার তৈরি করুন
    updater = Updater(BOT_TOKEN)
    
    # ডিসপ্যাচার পান
    dispatcher = updater.dispatcher
    
    # কমান্ড হ্যান্ডলার যোগ করুন
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("like", like_command))
    dispatcher.add_handler(CommandHandler("auto", auto_command))
    dispatcher.add_handler(CommandHandler("myautos", myautos_command))
    dispatcher.add_handler(CommandHandler("removeauto", removeauto_command))
    dispatcher.add_handler(CommandHandler("stauto", stauto_command))
    dispatcher.add_handler(CommandHandler("permitlike", permitlike_command))
    dispatcher.add_handler(CommandHandler("permitauto", permitauto_command))
    dispatcher.add_handler(CommandHandler("rmlike", rmlike_command))
    dispatcher.add_handler(CommandHandler("rmauto", rmauto_command))
    dispatcher.add_handler(CommandHandler("setlimit", setlimit_command))
    dispatcher.add_handler(CommandHandler("removelimit", removelimit_command))
    dispatcher.add_handler(CommandHandler("viewlimits", viewlimits_command))
    dispatcher.add_handler(CommandHandler("stats", stats_command))
    
    # ইরর হ্যান্ডলার যোগ করুন
    dispatcher.add_error_handler(error_handler)
    
    # সকাল ৭টায় অটো লাইক সেট করুন (বাংলাদেশ সময়)
    job_queue = updater.job_queue
    job_queue.run_daily(scheduled_auto_likes, time=datetime.time(7, 0, 0, tzinfo=timezone('Asia/Dhaka')))
    
    # বট চালু করুন
    updater.start_polling()
    logger.info("Bot started successfully!")
    
    # বট চালান
    updater.idle()

if __name__ == "__main__":
    main()
