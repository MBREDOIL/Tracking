#==========u==========
import os import environ
import hashlib
import aiohttp
import aiosqlite
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import difflib
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

# Configuration
API_ID = "22182189"
API_HASH = "5e7c4088f8e23d0ab61e29ae11960bf5"
BOT_TOKEN = environ.get("BOT_TOKEN", "")
OWNER_ID = 6556141430
DB_NAME = "pyro_tracker.db"
CHECK_INTERVAL = 30  # seconds

class TrackBot(Client):
    def __init__(self):
        super().__init__(
            "tracker_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        self.playwright = None
        self.browser = None
        self.context = None
    
    async def start(self):
        await super().start()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.context = await self.browser.new_context()
        await self.init_db()
        self.setup_scheduler()
        print("Bot Started!")
    
    async def stop(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        await super().stop()
    
    async def init_db(self):
        async with aiosqlite.connect(DB_NAME) as db:
            # Trackers Table
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
            
            # Admins Table
            await db.execute('''CREATE TABLE IF NOT EXISTS admins(
                user_id INTEGER PRIMARY KEY,
                role TEXT CHECK(role IN ('owner', 'admin')),
                username TEXT,
                added_by INTEGER,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            
            # Initialize Owner
            await db.execute('''INSERT OR IGNORE INTO admins 
                             (user_id, role, username, added_by)
                             VALUES (?, ?, ?, ?)''',
                             (OWNER_ID, 'owner', 'Owner', OWNER_ID))
            await db.commit()
    
    def setup_scheduler(self):
        async def scheduler():
            while True:
                await asyncio.sleep(CHECK_INTERVAL)
                await check_trackers()
        
        self.loop.create_task(scheduler())

app = TrackBot()

# Helper Functions
async def get_website_data(url: str, mode: str, selector: str = None):
    if mode == 'element':
        page =.new_page()
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_load_state("networkidle")
            
            result = {}
            if selector:
                element = await page.query_selector(selector)
                if element:
                    result['content'] = await element.inner_text()
                    result['screenshot'] = await element.screenshot(type="jpeg")
                else:
                    return {"error": "Element not found"}
            else:
                result['content'] = await page.content()
                result['screenshot'] = await page.screenshot(full_page=True, type="jpeg")
            return result
        finally:
            await page.close()
    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    if selector:
                        elements = soup.select(selector)
                        content = "\n".join([e.get_text() for e in elements])
                    else:
                        content = html
                    return {"content": content}
        except Exception as e:
            return {"error": str(e)}

async def create_diff(old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        lineterm='',
        fromfile='Old',
        tofile='New'
    )
    return '\n'.join(diff)[:4000]

# Admin Management
async def is_owner(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT role FROM admins WHERE user_id=?', (user_id,))
        result = await cursor.fetchone()
        return result and result[0] == 'owner' if result else False

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
        return bool(await cursor.fetchone())

# New Feature: Tracking Control Commands
@app.on_message(filters.command("addadmin"))
async def add_admin(client: Client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Owner access required!")
        return
    
    try:
        target = message.command[1]
        if target.startswith("@"):
            username = target[1:]
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute('SELECT user_id FROM admins WHERE username=?', (username,))
                user = await cursor.fetchone()
                if not user:
                    return await message.reply("User not found!")
                user_id = user[0]
        else:
            user_id = int(target)
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''INSERT OR REPLACE INTO admins 
                             (user_id, role, added_by) 
                             VALUES (?, ?, ?)''',
                             (user_id, 'admin', message.from_user.id))
            await db.commit()
        
        await message.reply(f"✅ Admin added successfully!\nUser ID: {user_id}")
    
    except (IndexError, ValueError):
        await message.reply("Usage: /addadmin [user_id/@username]")

@app.on_message(filters.command("removeadmin"))
async def remove_admin(client: Client, message: Message):
    if not await is_owner(message.from_user.id):
        await message.reply("❌ Owner access required!")
        return
    
    try:
        user_id = int(message.command[1])
        if user_id == OWNER_ID:
            return await message.reply("❌ Cannot remove owner!")
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('DELETE FROM admins WHERE user_id=?', (user_id,))
            await db.commit()
        
        await message.reply(f"✅ Admin removed successfully!\nUser ID: {user_id}")
    
    except (IndexError, ValueError):
        await message.reply("Usage: /removeadmin [user_id]")

@app.on_message(filters.command("admins"))
async def list_admins(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT user_id, role, username FROM admins')
        admins = await cursor.fetchall()
    
    admin_list = ["👥 **Admin List:**"]
    for admin in admins:
        admin_list.append(
            f"\n🆔 `{admin[0]}` | Role: {admin[1].capitalize()}"
            f"\n👤 Username: @{admin[2] or 'N/A'}"
        )
    
    await message.reply("\n".join(admin_list))

# New Feature: Tracking Status and Control
@app.on_message(filters.command("status"))
async def tracking_status(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Global Stats
        cursor = await db.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN datetime(next_check) <= datetime('now') THEN 1 ELSE 0 END) as pending
            FROM trackers
            WHERE user_id=?
        ''', (user_id,))
        stats = await cursor.fetchone()
        
        # Recent Trackers
        cursor = await db.execute('''
            SELECT url, status, next_check 
            FROM trackers 
            WHERE user_id=?
            ORDER BY id DESC 
            LIMIT 5
        ''', (user_id,))
        trackers = await cursor.fetchall()
    
    status_msg = [
        "📊 **Tracking Status:**",
        f"• Total Trackers: `{stats[0]}`",
        f"• Active Trackers: `{stats[1]}`",
        f"• Pending Checks: `{stats[2]}`",
        "\n🔍 **Recent Trackers:**"
    ]
    
    for tracker in trackers:
        status_msg.append(
            f"\n🌐 [{tracker[0]}]({tracker[0]})"
            f"\n├ Status: `{tracker[1].capitalize()}`"
            f"\n└ Next Check: `{tracker[2]}`"
        )
    
    await message.reply("\n".join(status_msg), disable_web_page_preview=True)

@app.on_message(filters.command("control"))
async def tracker_control(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        return
    
    args = message.command[1:]
    if len(args) < 2:
        return await message.reply("Usage: /control [url] [pause/resume/delete]")
    
    url = args[0]
    action = args[1].lower()
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT id FROM trackers WHERE url=? AND user_id=?', 
                                (url, user_id))
        tracker = await cursor.fetchone()
        
        if not tracker:
            return await message.reply("❌ Tracker not found!")
        
        if action == "pause":
            await db.execute('UPDATE trackers SET status="paused" WHERE id=?', (tracker[0],))
            msg = "⏸ Tracking paused"
        elif action == "resume":
            await db.execute('UPDATE trackers SET status="active" WHERE id=?', (tracker[0],))
            msg = "▶️ Tracking resumed"
        elif action == "delete":
            await db.execute('DELETE FROM trackers WHERE id=?', (tracker[0],))
            msg = "❌ Tracker deleted"
        else:
            return await message.reply("Invalid action! Use pause/resume/delete")
        
        await db.commit()
    
    await message.reply(f"✅ {msg}\nURL: {url}")

# Enhanced Tracking Commands
@app.on_message(filters.command("track"))
async def track(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("❌ Access Denied")
        return
    
    args = message.text.split()[1:]
    parser = re.compile(r'(--\w+)\s+([^ ]+)')
    options = dict(parser.findall(" ".join(args)))
    
    url = args[0] if args else None
    mode = options.get('--mode', 'hash')
    selector = options.get('--selector')
    interval = int(options.get('--interval', CHECK_INTERVAL))
    
    if not url:
        await message.reply("Please provide a URL")
        return
    
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        await message.reply("Invalid URL")
        return
    
    data = await get_website_data(url, mode, selector)
    if 'error' in data:
        await message.reply(f"Initial check failed: {data['error']}")
        return
    
    new_content = data.get('content', '')
    new_hash = hashlib.sha256(new_content.encode()).hexdigest()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''INSERT INTO trackers 
                        (url, user_id, mode, selector, interval, last_hash, last_content, next_check)
                        VALUES (?,?,?,?,?,?,?,datetime('now'))''',
                        (url, user_id, mode, selector, interval, new_hash, new_content))
        await db.commit()
    
    await message.reply(f"✅ Tracking started for:\n{url}\nMode: {mode}\nInterval: {interval}s")

@app.on_message(filters.command("check"))
async def check_single(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("❌ Access Denied")
        return
    
    url = ' '.join(message.command[1:])
    if not url:
        await message.reply("Usage: /check <url>")
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT * FROM trackers WHERE url=? AND user_id=?', 
                                (url, user_id))
        tracker = await cursor.fetchone()
    
    if not tracker:
        await message.reply("URL not being tracked")
        return
    
    data = await get_website_data(url, tracker[3], tracker[4])
    if 'error' in data:
        await message.reply(f"Error: {data['error']}")
        return
    
    new_content = data.get('content', '')
    new_hash = hashlib.sha256(new_content.encode()).hexdigest()
    
    if new_hash != tracker[6]:
        diff = await create_diff(tracker[7] or '', new_content)
        msg = f"🚨 Changes detected!\n{url}\n\nDiff:\n{diff}"
        
        if 'screenshot' in data:
            await message.reply_photo(
                photo=data['screenshot'],
                caption=msg
            )
        else:
            await message.reply(msg)
    else:
        await message.reply(f"✅ No changes: {url}")

# Periodic Checker with Status Management
async def check_trackers():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT * FROM trackers 
                                   WHERE status="active" 
                                   AND datetime(next_check) <= datetime('now')''')
        trackers = await cursor.fetchall()
        
        for tracker in trackers:
            tracker_id, url, user_id, mode, selector, interval, last_hash, last_content, next_check = tracker
            
            data = await get_website_data(url, mode, selector)
            if 'error' in data:
                await app.send_message(user_id, f"⚠️ Error checking {url}:\n{data['error']}")
                continue
            
            new_content = data.get('content', '')
            new_hash = hashlib.sha256(new_content.encode()).hexdigest()
            
            if new_hash != last_hash:
                diff = await create_diff(last_content or '', new_content)
                msg = f"🚨 Change detected!\n{url}\n\nDiff:\n{diff}"
                
                if 'screenshot' in data:
                    await app.send_photo(
                        user_id,
                        photo=data['screenshot'],
                        caption=msg
                    )
                else:
                    await app.send_message(user_id, msg)
                
                await db.execute('''UPDATE trackers SET 
                                  last_hash=?, 
                                  last_content=?,
                                  next_check=datetime('now', ? || ' seconds')
                                  WHERE id=?''',
                                  (new_hash, new_content, interval, tracker_id))
                await db.commit()

# Run the bot
if __name__ == "__main__":
    app.run()
