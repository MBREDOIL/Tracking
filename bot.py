#==========u==========
import os
import hashlib
import aiohttp
import aiosqlite
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaPhoto
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import difflib
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

# Configuration
API_ID = 1234567
API_HASH = "your_api_hash"
BOT_TOKEN = "your_bot_token"
OWNER_ID = 123456789
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
                next_check DATETIME)''')
            
            await db.execute('''CREATE TABLE IF NOT EXISTS admins(
                user_id INTEGER PRIMARY KEY,
                role TEXT,
                username TEXT,
                added_by INTEGER,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
            
            await db.execute('''INSERT OR IGNORE INTO admins 
                             (user_id, role, username, added_by)
                             VALUES (?, ?, ?, ?)''',
                             (OWNER_ID, 'owner', 'Owner', OWNER_ID))
            await db.commit()

app = TrackBot()

# Helper Functions
async def get_website_data(url: str, mode: str, selector: str = None):
    if mode == 'element':
        page = await app.context.new_page()
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

# Handlers
@app.on_message(filters.command("start"))
async def start(client: Client, message: Message):
    if not await is_admin(message.from_user.id):
        await message.reply("❌ Access Denied")
        return
    
    await message.reply(
        "🔍 Advanced Website Tracker Bot\n\n"
        "Commands:\n"
        "/track <url> [--mode hash|text|element] [--selector CSS] [--interval SECONDS]\n"
        "/untrack <url>\n/list\n/help"
    )

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

# Periodic Checker
async def check_trackers():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT * FROM trackers 
                                   WHERE datetime(next_check) <= datetime('now')''')
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
