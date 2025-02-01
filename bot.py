#==========u==========
import asyncio
import hashlib
import aiohttp
import aiosqlite
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import difflib
from datetime import datetime, timedelta
import re

# Configuration
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
OWNER_ID = 123456789  # Your Telegram User ID
ADMIN_ROLES = ['owner', 'admin']
DB_NAME = "tracker.db"
REQUEST_TIMEOUT = 15
BROWSER_TIMEOUT = 60000

class AsyncTracker:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None

    async def setup(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.context = await self.browser.new_context()

    async def get_rendered_content(self, url: str, selector: str = None):
        try:
            page = await self.context.new_page()
            await page.goto(url, timeout=BROWSER_TIMEOUT)
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
        except Exception as e:
            return {"error": str(e)}
        finally:
            await page.close()

    async def close(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Create trackers table
        await db.execute('''CREATE TABLE IF NOT EXISTS trackers
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             url TEXT NOT NULL,
             user_id INTEGER NOT NULL,
             mode TEXT CHECK(mode IN ('hash', 'text', 'element')) NOT NULL,
             selector TEXT,
             interval INTEGER NOT NULL,
             last_hash TEXT,
             last_content TEXT,
             next_check DATETIME)''')
        
        # Create admins table
        await db.execute('''CREATE TABLE IF NOT EXISTS admins
            (user_id INTEGER PRIMARY KEY,
             role TEXT NOT NULL,
             username TEXT,
             added_by INTEGER,
             added_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        
        # Initialize owner
        await db.execute('''INSERT OR IGNORE INTO admins 
                          (user_id, role, username, added_by)
                          VALUES (?, ?, ?, ?)''',
                          (OWNER_ID, 'owner', 'Owner', OWNER_ID))
        await db.commit()

async def get_website_data(url: str, mode: str, selector: str = None):
    if mode == 'element':
        tracker = AsyncTracker()
        await tracker.setup()
        result = await tracker.get_rendered_content(url, selector)
        await tracker.close()
        return result
    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
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

async def check_trackers(context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT * FROM trackers 
                                   WHERE datetime(next_check) <= datetime('now')''')
        trackers = await cursor.fetchall()
        
        for tracker in trackers:
            tracker_id, url, user_id, mode, selector, interval, last_hash, last_content, next_check = tracker
            
            try:
                data = await get_website_data(url, mode, selector)
                if 'error' in data:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ Error checking {url}:\n{data['error']}"
                    )
                    continue

                new_content = data.get('content', '')
                new_hash = hashlib.sha256(new_content.encode()).hexdigest()

                if new_hash != last_hash:
                    diff_text = await create_diff(last_content or '', new_content)
                    message = f"🚨 Change detected!\n{url}\n\nDiff:\n{diff_text}"

                    if 'screenshot' in data:
                        await context.bot.send_media_group(
                            chat_id=user_id,
                            media=[InputMediaPhoto(data['screenshot'], caption=message)]
                        )
                    else:
                        await context.bot.send_message(chat_id=user_id, text=message)

                    # Update database
                    await db.execute('''UPDATE trackers SET 
                                      last_hash=?, 
                                      last_content=?,
                                      next_check=datetime('now', ? || ' seconds')
                                      WHERE id=?''',
                                      (new_hash, new_content, interval, tracker_id))
                    await db.commit()
                else:
                    # Reschedule without changes
                    await db.execute('''UPDATE trackers SET 
                                      next_check=datetime('now', ? || ' seconds')
                                      WHERE id=?''',
                                      (interval, tracker_id))
                    await db.commit()

            except Exception as e:
                print(f"Error processing {url}: {str(e)}")

async def is_owner(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT role FROM admins WHERE user_id=?''', (user_id,))
        result = await cursor.fetchone()
        return result and result[0] == 'owner' if result else False

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT 1 FROM admins WHERE user_id=?''', (user_id,))
        return bool(await cursor.fetchone())

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access Denied")
        return
    
    await update.message.reply_text(
        "🔍 Advanced Website Tracker Bot\n\n"
        "Commands:\n"
        "/track <url> [--mode hash|text|element] [--selector CSS] [--interval SECONDS]\n"
        "/untrack <url>\n"
        "/list\n"
        "/help - Show all commands"
    )

async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        await update.message.reply_text("❌ Access Denied")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Please provide a URL")
        return

    # Parse command arguments
    parser = re.compile(r'(--\w+)\s+([^ ]+)')
    options = dict(parser.findall(" ".join(args)))
    
    url = args[0]
    mode = options.get('--mode', 'hash')
    selector = options.get('--selector')
    interval = int(options.get('--interval', 300))

    # Validate URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        await update.message.reply_text("Invalid URL format")
        return

    # Initial check
    data = await get_website_data(url, mode, selector)
    if 'error' in data:
        await update.message.reply_text(f"Initial check failed: {data['error']}")
        return

    # Store in database
    new_content = data.get('content', '')
    new_hash = hashlib.sha256(new_content.encode()).hexdigest()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''INSERT INTO trackers 
                        (url, user_id, mode, selector, interval, last_hash, last_content, next_check)
                        VALUES (?,?,?,?,?,?,?,datetime('now'))''',
                        (url, user_id, mode, selector, interval, new_hash, new_content))
        await db.commit()
    
    await update.message.reply_text(
        f"✅ Tracking started:\n{url}\n"
        f"Mode: {mode}\n"
        f"Interval: {interval}s"
    )

async def untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        await update.message.reply_text("❌ Access Denied")
        return

    url = " ".join(context.args)
    if not url:
        await update.message.reply_text("Please provide a URL")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM trackers WHERE url=? AND user_id=?", (url, user_id))
        await db.commit()
    
    await update.message.reply_text(f"❌ Stopped tracking: {url}")

async def list_trackers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        await update.message.reply_text("❌ Access Denied")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT url, mode, interval, next_check 
                                   FROM trackers WHERE user_id=?''', (user_id,))
        trackers = await cursor.fetchall()
    
    if not trackers:
        await update.message.reply_text("No active trackers")
        return
    
    message = ["Active Trackers:"]
    for url, mode, interval, next_check in trackers:
        message.append(
            f"\n🔗 {url}\n"
            f"Mode: {mode} | Interval: {interval}s\n"
            f"Next check: {next_check}"
        )
    
    await update.message.reply_text("\n".join(message))

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access Denied")
        return
    
    await update.message.reply_text("🔍 Checking all websites now...")
    await check_trackers(context)
    await update.message.reply_text("✅ All checks completed")

async def check_single(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        await update.message.reply_text("❌ Access Denied")
        return

    url = " ".join(context.args)
    if not url:
        await update.message.reply_text("Usage: /check <url>")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT * FROM trackers WHERE url=? AND user_id=?''', (url, user_id))
        tracker = await cursor.fetchone()
    
    if not tracker:
        await update.message.reply_text("This URL is not being tracked")
        return

    try:
        tracker_id, url, user_id, mode, selector, interval, last_hash, last_content, next_check = tracker
        data = await get_website_data(url, mode, selector)
        
        if 'error' in data:
            await update.message.reply_text(f"⚠️ Check error:\n{data['error']}")
            return

        new_content = data.get('content', '')
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()

        if new_hash != last_hash:
            diff_text = await create_diff(last_content or '', new_content)
            message = f"🚨 Manual check detected changes!\n{url}\n\nDiff:\n{diff_text}"
            
            if 'screenshot' in data:
                await context.bot.send_media_group(
                    chat_id=user_id,
                    media=[InputMediaPhoto(data['screenshot'], caption=message)]
                )
            else:
                await context.bot.send_message(chat_id=user_id, text=message)
        else:
            await update.message.reply_text(f"✅ No changes detected: {url}")

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def system_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access Denied")
        return
    
    status_report = []
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Trackers statistics
        cursor = await db.execute('''SELECT 
            COUNT(id) as total,
            SUM(CASE WHEN datetime(next_check) <= datetime('now') THEN 1 ELSE 0 END) as pending,
            COUNT(DISTINCT url) as unique_urls
            FROM trackers''')
        stats = await cursor.fetchone()
        
        # Active trackers list
        cursor = await db.execute('''SELECT url, mode, next_check FROM trackers LIMIT 10''')
        recent_trackers = await cursor.fetchall()

    status_report.append(
        f"📊 System Status:\n"
        f"• Total Trackers: {stats[0]}\n"
        f"• Pending Checks: {stats[1]}\n"
        f"• Unique URLs: {stats[2]}\n"
        f"• Recent Trackers:"
    )
    
    for url, mode, next_check in recent_trackers:
        status_report.append(f"\n  - {url} ({mode}) | Next check: {next_check}")

    await update.message.reply_text("\n".join(status_report))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 Bot Commands:

/start - Basic information
/help - Show all commands

🔍 Tracking Commands:
/track <url> [options] - Start tracking a website
/untrack <url> - Stop tracking
/list - Show active trackers

⚡ Instant Actions:
/checknow - Check all trackers immediately
/check <url> - Check specific URL

📈 Status Commands:
/status - System status
/admins - List administrators

🛠️ Options:
--mode hash/text/element
--selector CSS_SELECTOR
--interval SECONDS

Examples:
/track https://example.com --mode element --selector ".price" --interval 300
"""
    await update.message.reply_text(help_text)

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_owner(user.id):
        await update.message.reply_text("❌ Only owner can use this command")
        return

    try:
        target_user = context.args[0]
        if target_user.startswith('@'):
            username = target_user[1:]
            async with aiosqlite.connect(DB_NAME) as db:
                cursor = await db.execute('''SELECT user_id FROM admins WHERE username=?''', (username,))
                result = await cursor.fetchone()
                if not result:
                    await update.message.reply_text("⚠️ User not found")
                    return
                target_id = result[0]
        else:
            target_id = int(target_user)

        if await is_admin(target_id):
            await update.message.reply_text("ℹ️ User is already admin")
            return

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute('''INSERT INTO admins 
                              (user_id, role, username, added_by)
                              VALUES (?,?,?,?)''',
                              (target_id, 'admin', '', user.id))
            await db.commit()
        
        await update.message.reply_text(f"✅ New admin added\nUser ID: {target_id}")

    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: /addadmin <user_id/username>")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_owner(user.id):
        await update.message.reply_text("❌ Only owner can use this command")
        return

    try:
        target_id = int(context.args[0])
        if target_id == OWNER_ID:
            await update.message.reply_text("❌ Cannot remove owner")
            return

        async with aiosqlite.connect(DB_NAME) as db:
            cursor = await db.execute('''DELETE FROM admins WHERE user_id=?''', (target_id,))
            await db.commit()
        
        if cursor.rowcount > 0:
            await update.message.reply_text(f"✅ Admin removed\nUser ID: {target_id}")
        else:
            await update.message.reply_text("⚠️ User is not admin")

    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ Usage: /removeadmin <user_id>")

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Access Denied")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''SELECT user_id, role, username FROM admins''')
        admins = await cursor.fetchall()

    admin_list = ["👥 Admin List:"]
    for admin in admins:
        admin_list.append(
            f"\n🆔 {admin[0]} | Role: {admin[1]}"
            f"\n👤 Username: @{admin[2] if admin[2] else 'N/A'}"
        )

    await update.message.reply_text("\n".join(admin_list))

async def main():
    await init_db()
    
    application = Application.builder().token(TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("untrack", untrack))
    application.add_handler(CommandHandler("list", list_trackers))
    application.add_handler(CommandHandler("checknow", check_now))
    application.add_handler(CommandHandler("check", check_single))
    application.add_handler(CommandHandler("status", system_status))
    application.add_handler(CommandHandler("admins", list_admins))
    application.add_handler(CommandHandler("addadmin", add_admin))
    application.add_handler(CommandHandler("removeadmin", remove_admin))

    # Schedule periodic checks every 30 seconds
    job_queue = application.job_queue
    job_queue.run_repeating(check_trackers, interval=30, first=10)

    # Start the bot
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())