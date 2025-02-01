#==========u==========
import os
import hashlib
import aiohttp
import aiosqlite
import asyncio
import difflib
import psutil
from datetime import datetime
from urllib.parse import urlparse
from typing import List, Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# Configuration
API_ID = int(os.getenv("API_ID", 22182189))
API_HASH = os.getenv("API_HASH", "5e7c4088f8e23d0ab61e29ae11960bf5")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", 6556141430))
DB_NAME = os.getenv("DB_NAME", "tracker.db")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
MAX_TRACKERS_PER_USER = 20

class TrackBot(Client):
    def __init__(self):
        super().__init__(
            "tracker_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        self.browser = None
        self.start_time = datetime.now()
        self.silent_mode = {}

    app = TrackBot()

    async def start(self):
        await super().start()
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        self.browser = webdriver.Chrome(service=ChromeService(), options=chrome_options)
        await self.init_db()
        await self.setup_scheduler()
        print("Bot Started!")

    async def stop(self):
        self.browser.quit()
        await super().stop()

    async def init_db(self):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS trackers(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                mode TEXT CHECK(mode IN ('hash', 'text', 'element')),
                selector TEXT,
                interval INTEGER,
                last_hash TEXT,
                last_content TEXT,
                next_check DATETIME,
                status TEXT CHECK(status IN ('active', 'paused')) DEFAULT 'active')''')
            
            await db.execute('''CREATE TABLE IF NOT EXISTS admins(
                user_id INTEGER PRIMARY KEY,
                role TEXT CHECK(role IN ('owner', 'admin')),
                username TEXT,
                added_by INTEGER,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

            await db.execute('''INSERT OR IGNORE INTO admins 
                             (user_id, role, username, added_by)
                             VALUES (?, ?, ?, ?)''',
                             (OWNER_ID, 'owner', 'Owner', OWNER_ID))
            await db.commit()

    async def setup_scheduler(self):
        async def scheduler():
            while True:
                await asyncio.sleep(CHECK_INTERVAL)
                await self.check_trackers()
        self.loop.create_task(scheduler())

    async def check_trackers(self):
        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('''SELECT * FROM trackers 
                                       WHERE status="active" 
                                       AND datetime(next_check) <= datetime('now')''')
            trackers = await cursor.fetchall()

            for tracker in trackers:
                tracker_data = self.parse_tracker(tracker)
                try:
                    data = await self.get_website_data(tracker_data['url'], 
                                                     tracker_data['mode'], 
                                                     tracker_data['selector'])
                    if 'error' in data:
                        await self.handle_tracker_error(tracker_data, data['error'])
                        continue

                    new_hash = hashlib.sha256(data['content'].encode()).hexdigest()
                    if new_hash != tracker_data['last_hash']:
                        await self.handle_content_change(db, tracker_data, data)
                    await self.update_tracker_check_time(db, tracker_data)
                    await db.commit()
                except Exception as e:
                    print(f"Error processing {tracker_data['url']}: {str(e)}")

    async def handle_content_change(self, db, tracker, data):
        diff = await create_diff(tracker['last_content'] or '', data['content'])
        msg = f"🚨 Change detected!\n{tracker['url']}\n\nDiff:\n{diff}"

        if 'screenshot' in data and not self.silent_mode.get(tracker['user_id']):
            await self.send_photo(tracker['user_id'], photo=data['screenshot'], caption=msg)
        else:
            await self.send_message(tracker['user_id'], msg)

        await db.execute('''UPDATE trackers SET 
                          last_hash=?, last_content=?,
                          next_check=datetime('now', ? || ' seconds')
                          WHERE id=?''',
                          (hashlib.sha256(data['content'].encode()).hexdigest(),
                           data['content'],
                           tracker['interval'],
                           tracker['id']))

    async def get_website_data(self, url: str, mode: str, selector: str = None):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        return {"error": f"HTTP Error {response.status}"}

                    content = await response.text()
                    soup = BeautifulSoup(content, "html.parser")
                    result = {'content': content}

                    if mode == 'element' and selector:
                        if element := soup.select_one(selector):
                            result['content'] = element.get_text(strip=True)
                        else:
                            return {"error": "Element not found"}

                    self.browser.get(url)
                    if mode == 'element' and selector:
                        element = self.browser.find_element(By.CSS_SELECTOR, selector)
                        result['screenshot'] = element.screenshot_as_png
                    else:
                        result['screenshot'] = self.browser.get_screenshot_as_png()

                    return result
        except Exception as e:
            return {"error": str(e)}

# Command Handlers
@app.on_message(filters.command("start"))
async def start_command(client: TrackBot, message: Message):
    await message.reply(
        "🌐 **Web Tracker Bot**\n"
        "Advanced website monitoring solution\n\n"
        "Use /help for commands list",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Tracker", callback_data="add_tracker"),
             InlineKeyboardButton("📋 My Trackers", callback_data="list_trackers")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
             InlineKeyboardButton("❓ Help", callback_data="help")]
        ])
    )

@app.on_message(filters.command("help"))
async def help_command(client: TrackBot, message: Message):
    help_text = """
📚 **Command Reference**

🔹 **Basic Commands**
/start - Initialize bot
/help - Show this message

🔹 **Tracking Management**
/add <url> <mode> [selector] [interval] - Add new tracker
/remove <id> - Remove tracker
/list - List your trackers
/details <id> - Show tracker details

🔹 **Admin Tools** 
/silent - Toggle notifications
/stats - System statistics
/mystats - Personal statistics

🔹 **Owner Commands**
/addadmin @username - Add new admin
/removeadmin @username - Remove admin

📌 **Modes:** hash, text, element
📌 **Example:**
/add https://example.com element div#content 300
    """
    await message.reply(help_text)

@app.on_message(filters.command("add"))
async def add_tracker_handler(client: TrackBot, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("🔒 Admin access required!")

    args = message.text.split()[1:]
    if len(args) < 2:
        return await message.reply("❗ Usage: /add <url> <mode> [selector] [interval]")

    url = args[0]
    if not is_valid_url(url):
        return await message.reply("❌ Invalid URL format!")

    user_id = message.from_user.id
    if await count_user_trackers(user_id) >= MAX_TRACKERS_PER_USER:
        return await message.reply(f"❌ Tracker limit reached ({MAX_TRACKERS_PER_USER})")

    try:
        tracker_id = await store_tracker(
            user_id=user_id,
            url=url,
            mode=args[1],
            selector=args[2] if len(args) > 2 else None,
            interval=int(args[3]) if len(args) > 3 else 300
        )
        await message.reply(f"✅ Tracker added!\nID: `{tracker_id}`\nURL: {url}")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@app.on_message(filters.command(["remove", "delete"]))
async def remove_tracker_handler(client: TrackBot, message: Message):
    user_id = message.from_user.id
    args = message.text.split()[1:]
    
    if not args:
        return await message.reply("❗ Usage: /remove <tracker_id>")

    try:
        tracker_id = int(args[0])
        tracker = await get_tracker(tracker_id)
        
        if not tracker:
            return await message.reply("❌ Tracker not found!")
            
        if user_id != tracker['user_id'] and not await is_admin(user_id):
            return await message.reply("🔒 Can't remove others' trackers!")

        await delete_tracker(tracker_id)
        await message.reply(f"✅ Tracker removed!\nID: `{tracker_id}`")
        
    except ValueError:
        await message.reply("❌ Invalid tracker ID format!")

@app.on_message(filters.command("stats"))
async def show_stats(client: TrackBot, message: Message):
    if not await is_admin(message.from_user.id):
        return await message.reply("🔒 Admin access required!")

    stats = await get_system_stats(client)
    response = (
        "📊 **System Statistics**\n\n"
        f"• Total Users: `{stats['total_users']}`\n"
        f"• Active Trackers: `{stats['active_trackers']}`\n"
        f"• Paused Trackers: `{stats['paused_trackers']}`\n"
        f"• Memory Usage: `{stats['memory_usage']} MB`\n"
        f"• Uptime: `{stats['uptime']}`"
    )
    await message.reply(response)

# Admin Management
@app.on_message(filters.command("addadmin"))
async def add_admin(client: TrackBot, message: Message):
    if not await is_owner(message.from_user.id):
        return await message.reply("🔒 Owner access required!")

    try:
        target = message.command[1]
        if target.startswith("@"):
            user = await client.get_users(target[1:])
            user_id = user.id
            username = user.username
        else:
            user_id = int(target)
            user = await client.get_users(user_id)
            username = user.username

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''INSERT OR REPLACE INTO admins 
                             (user_id, role, username, added_by)
                             VALUES (?, ?, ?, ?)''',
                             (user_id, 'admin', username, message.from_user.id))
            await db.commit()

        await message.reply(f"✅ Admin added!\nUser: @{username}\nID: `{user_id}`")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@app.on_message(filters.command(["removeadmin", "deladmin"]))
async def remove_admin(client: TrackBot, message: Message):
    if not await is_owner(message.from_user.id):
        return await message.reply("🔒 Owner access required!")

    try:
        target = message.command[1]
        if target.startswith("@"):
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute('SELECT user_id FROM admins WHERE username=?', (target[1:],))
                user = await cursor.fetchone()
                if not user:
                    return await message.reply("❌ User not found!")
                user_id = user[0]
        else:
            user_id = int(target)

        if user_id == OWNER_ID:
            return await message.reply("❌ Cannot remove owner!")

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('DELETE FROM admins WHERE user_id=?', (user_id,))
            await db.commit()

        await message.reply(f"✅ Admin removed!\nID: `{user_id}`")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

# Helper Functions
async def create_diff(old: str, new: str) -> str:
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", 
                              fromfile="Old", tofile="New")
    return '\n'.join(diff)[:4000]

def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

async def get_system_stats(client: TrackBot) -> Dict:
    async with aiosqlite.connect(DB_NAME) as db:
        return {
            'total_users': (await (await db.execute('SELECT COUNT(DISTINCT user_id) FROM trackers')).fetchone())[0],
            'active_trackers': (await (await db.execute('SELECT COUNT(*) FROM trackers WHERE status="active"')).fetchone())[0],
            'paused_trackers': (await (await db.execute('SELECT COUNT(*) FROM trackers WHERE status="paused"')).fetchone())[0],
            'memory_usage': round(psutil.Process().memory_info().rss / 1024 / 1024, 1),
            'uptime': str(datetime.now() - client.start_time).split('.')[0]
        }

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
        return bool(await cursor.fetchone())

async def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

if __name__ == "__main__":
    app = TrackBot()
    app.run()
