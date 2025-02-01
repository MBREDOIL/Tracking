#==========u==========
import os
import hashlib
import aiohttp
import aiosqlite
import asyncio
import re
import difflib
from datetime import datetime, timedelta
from urllib.parse import urlparse

from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# Configuration
API_ID = int(os.getenv("API_ID", 1234567))
API_HASH = os.getenv("API_HASH", "your_api_hash")
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")
OWNER_ID = int(os.getenv("OWNER_ID", 123456789))
DB_NAME = os.getenv("DB_NAME", "tracker.db")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 30))

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
    
    def setup_scheduler(self):
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
                tracker_id, url, user_id, mode, selector, interval, last_hash, last_content, next_check, status = tracker
                
                try:
                    data = await self.get_website_data(url, mode, selector)
                    if 'error' in data:
                        await self.send_message(user_id, f"⚠️ Error checking {url}:\n{data['error']}")
                        continue

                    new_content = data.get('content', '')
                    new_hash = hashlib.sha256(new_content.encode()).hexdigest()

                    if new_hash != last_hash:
                        diff = await create_diff(last_content or '', new_content)
                        msg = f"🚨 Change detected!\n{url}\n\nDiff:\n{diff}"

                        if 'screenshot' in data:
                            await self.send_photo(
                                user_id,
                                photo=data['screenshot'],
                                caption=msg
                            )
                        else:
                            await self.send_message(user_id, msg)

                        await db.execute('''UPDATE trackers SET 
                                          last_hash=?, 
                                          last_content=?,
                                          next_check=datetime('now', ? || ' seconds')
                                          WHERE id=?''',
                                          (new_hash, new_content, interval, tracker_id))
                    else:
                        await db.execute('''UPDATE trackers SET 
                                          next_check=datetime('now', ? || ' seconds')
                                          WHERE id=?''',
                                          (interval, tracker_id))
                    
                    await db.commit()

                except Exception as e:
                    print(f"Error processing {url}: {str(e)}")

    async def get_website_data(self, url: str, mode: str, selector: str = None):
        if mode == 'element':
            page = await self.context.new_page()
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

app = TrackBot()

async def create_diff(old: str, new: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        lineterm='',
        fromfile='Old',
        tofile='New'
    )
    return '\n'.join(diff)[:4000]

# Admin Management Handlers
@app.on_message(filters.command("addadmin"))
async def add_admin(client: TrackBot, message: Message):
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

@app.on_message(filters.command(["removeadmin", "deladmin"]))
async def remove_admin(client: TrackBot, message: Message):
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

# Tracking Control Handlers
@app.on_message(filters.command("control"))
async def tracker_control(client: TrackBot, message: Message):
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

# Helper Functions
async def is_owner(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT role FROM admins WHERE user_id=?', (user_id,))
        result = await cursor.fetchone()
        return result and result[0] == 'owner' if result else False

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
        return bool(await cursor.fetchone())

if __name__ == "__main__":
    app.run()
