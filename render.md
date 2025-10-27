Free Fire Auto Like Bot

This is a Telegram bot written in Python to manage sending "likes" to Free Fire UIDs using a specific API. It includes a user permission system, admin controls, and a daily scheduler for auto-like tasks.

Features

Send likes manually with /like (requires permission)

Schedule daily auto-likes with /auto (requires permission)

View and manage your personal auto-like tasks

Admin panel to:

Grant/remove permissions (/permitlike, /rmlike, etc.)

Set custom like limits for users (/setlimit)

View bot statistics (/stats)

Daily tasks run automatically at 7:00 AM Bangladesh Time (UTC+6)

Stores all data in a local SQLite database file (bot_data.db)

1. Local Setup

Prerequisites

Python 3.7+

A Telegram Bot Token (Get one from @BotFather on Telegram)

Your numeric Telegram User ID (Get it from @userinfobot on Telegram)

Steps

Edit bot.py:
Open bot.py and fill in the configuration variables at the top:

BOT_TOKEN: Your bot token from BotFather.

ADMIN_IDS: A list containing your numeric Telegram ID. Example: ADMIN_IDS = [123456789]

Install Dependencies:

pip install -r requirements.txt


Run the Bot:

python bot.py


Your bot should now be online and responding on Telegram.

2. Deployment on Render (Free Tier)

This bot is designed to be easily deployed on Render's free tier, which supports background workers and persistent disks.

Steps

Sign up for Render: Create an account on render.com.

Fork this code: You need to have this code in your own GitHub repository.

Create a Persistent Disk:

Go to your Render Dashboard.

Click New -> Persistent Disk.

Give it a name (e.g., bot-db-disk).

Set the Mount Path to /data

Set the size (e.g., 1 GB is more than enough).

Click Create Persistent Disk.

Update bot.py for Render:

Before you push your code, change this line in bot.py:

# Change this:
DB_NAME = "bot_data.db"

# To this:
DB_NAME = "/data/bot_data.db"


This tells the bot to store its database file on the persistent disk you just created.

Commit and push this change to your GitHub repository.

Create a New Web Service:

On your Render Dashboard, click New -> Web Service.

Connect your GitHub account and select your bot's repository.

Give your service a name (e.g., ff-like-bot).

Environment: Select Python 3.

Region: Choose a region.

Build Command: pip install -r requirements.txt

Start Command: python bot.py

Select the Free Instance Type:

Scroll down to Instance Type and choose the Free plan.

IMPORTANT: A free Web Service will spin down after 15 minutes of inactivity. For a bot, this is not ideal. You might want to use a Background Worker instead of a Web Service, as they are designed to run continuously (and also have a free tier).

To use a Background Worker (Recommended):

Go to New -> Background Worker.

Follow the same steps as above (connect repo, set build/start commands). Background workers are better for bots.

Attach the Persistent Disk:

In your new service's settings, go to the Disks section.

Click Add Disk.

Select the disk you created in Step 3 (e.g., bot-db-disk).

The Mount Path should already be set to /data.

Click Save Changes.

Deploy:

Click Create Web Service (or Create Background Worker).

Render will pull your code, install the dependencies, and run your python bot.py start command.

You can view the logs to see the "Bot is polling..." message.

Your bot is now live and running on Render!
