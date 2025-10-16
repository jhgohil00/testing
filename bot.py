import os
import logging
import threading
import json
import re
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode

# --- Web Server to satisfy Render's health checks ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server_address = ('', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"Starting simple web server for health checks on port {port}")
    httpd.serve_forever()

# --- Configuration ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
RAZORPAY_LINK = os.environ.get("RAZORPAY_LINK", "https://razorpay.me/@gateprep?amount=CVDUr6Uxp2FOGZGwAHntNg%3D%3D")

# --- GitHub Gist Configuration for Persistent Data ---
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DB_URL = f"https://my-json-server.typicode.com/{GITHUB_USERNAME}/{GIST_ID}/db"

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def escape_markdown(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- Data Persistence ---
DB_DATA = {} # Global variable to hold all data in memory

def load_data_from_gist():
    """Loads the entire database from MyJSONServer into the global DB_DATA."""
    global DB_DATA
    if not GITHUB_USERNAME or not GIST_ID:
        logger.error("FATAL: GITHUB_USERNAME or GIST_ID environment variables not set.")
        DB_DATA = {"courses": {}, "stats": {"total_users": 0, "course_views": {}}, "users": []}
        return
    try:
        response = requests.get(DB_URL)
        response.raise_for_status()
        DB_DATA = response.json()
        logger.info("Successfully loaded database from Gist.")
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        logger.error(f"FATAL: Could not load data from Gist: {e}. Using empty DB.")
        DB_DATA = {"courses": {}, "stats": {"total_users": 0, "course_views": {}}, "users": []}

def save_data_to_gist():
    """Saves the entire in-memory database back to the GitHub Gist."""
    if not GITHUB_TOKEN or not GIST_ID:
        logger.error("GitHub Token or Gist ID is not set. Cannot save data.")
        return
    try:
        url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        data = {
            "files": {"db.json": {"content": json.dumps(DB_DATA, indent=4)}}
        }
        response = requests.patch(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info("Successfully saved data to Gist.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to save data to Gist: {e}")

def save_user_id(user_id):
    """Saves a new user's ID, avoids duplicates, and triggers a data save."""
    users = DB_DATA.setdefault('users', [])
    if user_id not in users:
        users.append(user_id)
        DB_DATA.setdefault('stats', {})['total_users'] = len(users)
        save_data_to_gist()

# --- Bot Texts ---
COURSE_DETAILS_TEXT = """
📚 *Course Details: {course_name}*

Here's what you get:
- Full Syllabus Coverage
- 250+ High-Quality Video Lectures
- Previous Year Questions (PYQs) Solved
- Comprehensive Test Series
- Regular Quizzes to Test Your Knowledge
- Weekly Current Affairs Updates
- Workbook & Study Materials
"""
BUY_COURSE_TEXT = """
✅ *You are about to purchase: {course_name}*

*Price: ₹{price}*

By purchasing, you will get full access to our private channel which includes:
- Full syllabus lectures
- 250+ video lectures
- Weekly current affairs
- Workbook, Books, PYQs
- Full Test Series

Please proceed with the payment. If you have already paid, share the screenshot with us.
"""
HELP_TEXT = """
👋 *Bot Help Guide*

Here's how to use me:

1️⃣ *Browse Courses*
- Use the buttons on the main menu to see details about each course.

2️⃣ *Talk to the Admin*
- Select a course, then click *"💬 Talk to Admin"*.
- Type and send your message. It will be forwarded to the admin.
- The admin's reply will be sent to you here.

3️⃣ *Buy a Course*
- After selecting a course, click *"🛒 Buy Full Course"*.
- Use the payment button to pay.
- After paying, click *"✅ Already Paid? Share Screenshot"* and send your payment screenshot.

If you have any issues, feel free to use the "Talk to Admin" feature.
"""
ADMIN_HELP_TEXT = """
👑 *Admin Panel Commands*

`/admin` - Show this panel.
`/listcourses` - List all courses.
`/addcourse <name>; <price>; <status>`
  _Ex: /addcourse New Course; 199; available_
`/editcourse <key>; <name>; <price>; <status>`
  _Ex: /editcourse new_course; "Adv Course"; 249; coming_soon_
`/delcourse <key>` - Remove a course.
`/set_order <key> <order_num>` - Change course display order.
  _Ex: /set_order new_course 1_
`/stats` - View bot usage statistics and user list.
`/broadcast <message>` - Send a message to all users.
`/reply <user_id> <message>` - Send a direct message to a user.

_Statuses: `available` or `coming_soon`_
"""

# --- Conversation States ---
SELECTING_ACTION, FORWARD_TO_ADMIN, FORWARD_SCREENSHOT = range(3)

# --- Helper to create main menu keyboard ---
def get_main_menu_keyboard():
    keyboard = []
    # Sort courses by the 'order' key, with a fallback for courses without it
    sorted_courses = sorted(DB_DATA.get('courses', {}).items(), key=lambda item: item[1].get('order', 999))
    
    for key, course in sorted_courses:
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=key)])
    return InlineKeyboardMarkup(keyboard)

# --- Command and Message Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    save_user_id(user.id)
    logger.info(f"User {user.first_name} ({user.id}) started the bot.")
    
    reply_markup = get_main_menu_keyboard()
    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\nI am your assistant for Mechanical Engineering courses. Please select a course to view details or use /help for instructions.",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    reply_markup = get_main_menu_keyboard()
    await query.edit_message_text(text="Please select a course to view details:", reply_markup=reply_markup)
    return SELECTING_ACTION

async def main_menu_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    reply_markup = get_main_menu_keyboard()
    await update.message.reply_text("You can select another course:", reply_markup=reply_markup)
    return SELECTING_ACTION

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

# --- Admin Commands ---
def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    await update.message.reply_text(ADMIN_HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

async def list_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    courses = DB_DATA.get('courses', {})
    if not courses:
        await update.message.reply_text("No courses defined. Use `/addcourse`.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    courses_info = "*📚 Current Courses:*\n\n"
    sorted_courses = sorted(courses.items(), key=lambda item: item[1].get('order', 999))
    for key, course in sorted_courses:
        courses_info += (
            f"*Key:* `{key}`\n"
            f"*Name:* {escape_markdown(course['name'])}\n"
            f"*Price:* ₹{course['price']}\n"
            f"*Status:* {escape_markdown(course.get('status', 'N/A').replace('_', ' ').title())}\n"
            f"*Order:* {course.get('order', 'Not Set')}\n"
            f"----------\n"
        )
    await update.message.reply_text(courses_info, parse_mode=ParseMode.MARKDOWN_V2)

async def add_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        args_str = " ".join(context.args)
        name, price_str, status = [p.strip() for p in args_str.split(';')]
        status = status.lower()
        if status not in ["available", "coming_soon"]:
            raise ValueError("Invalid status")
        price = int(price_str)
        if price < 0: raise ValueError("Negative price")

        key = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        original_key, counter = key, 1
        while key in DB_DATA['courses']:
            key = f"{original_key}_{counter}"
            counter += 1
        
        DB_DATA['courses'][key] = {"name": name, "price": price, "status": status, "order": len(DB_DATA['courses']) + 1}
        save_data_to_gist()
        await update.message.reply_text(f"✅ Course `{escape_markdown(name)}` (key: `{key}`) added.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in add_course: {e}")
        await update.message.reply_text("Usage: `/addcourse <name>; <price>; <status>`\n_Example: /addcourse New Course; 199; available_", parse_mode=ParseMode.MARKDOWN_V2)

async def edit_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        args_str = " ".join(context.args)
        key, new_name, new_price_str, new_status = [p.strip() for p in args_str.split(';')]
        new_status = new_status.lower()
        if key not in DB_DATA['courses']: raise ValueError("Course key not found")
        if new_status not in ["available", "coming_soon"]: raise ValueError("Invalid status")
        new_price = int(new_price_str)
        if new_price < 0: raise ValueError("Negative price")
        
        DB_DATA['courses'][key]['name'] = new_name
        DB_DATA['courses'][key]['price'] = new_price
        DB_DATA['courses'][key]['status'] = new_status
        save_data_to_gist()
        await update.message.reply_text(f"✅ Course `{key}` updated.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in edit_course: {e}")
        await update.message.reply_text("Usage: `/editcourse <key>; <new_name>; <new_price>; <new_status>`", parse_mode=ParseMode.MARKDOWN_V2)

async def delete_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        key = context.args[0]
        if key in DB_DATA['courses']:
            del DB_DATA['courses'][key]
            save_data_to_gist()
            await update.message.reply_text(f"✅ Course `{key}` deleted.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{key}` not found.", parse_mode=ParseMode.MARKDOWN_V2)
    except IndexError:
        await update.message.reply_text("Usage: `/delcourse <key>`", parse_mode=ParseMode.MARKDOWN_V2)

async def set_course_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        key, order_str = context.args
        order = int(order_str)
        if key in DB_DATA['courses']:
            DB_DATA['courses'][key]['order'] = order
            save_data_to_gist()
            await update.message.reply_text(f"✅ Order for course `{key}` set to {order}.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{key}` not found.", parse_mode=ParseMode.MARKDOWN_V2)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/set_order <key> <order_number>`", parse_mode=ParseMode.MARKDOWN_V2)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    stats = DB_DATA.get('stats', {})
    stats_text = f"📊 *Bot Statistics*\n\n*Total Users:* `{stats.get('total_users', 0)}`\n\n*Course Views:*\n"
    
    course_views = stats.get('course_views', {})
    if not course_views:
        stats_text += "  _No course views yet._\n"
    else:
        sorted_views = sorted(course_views.items(), key=lambda item: item[1], reverse=True)
        for key, views in sorted_views:
            course_name = DB_DATA.get('courses', {}).get(key, {}).get('name', f'Unknown ({key})')
            stats_text += f"  - {escape_markdown(course_name)}: `{views}` views\n"

    stats_text += "\n*User List:*\n"
    user_ids = DB_DATA.get('users', [])
    if not user_ids:
        stats_text += "  _No users have started the bot._\n"
    else:
        for user_id in user_ids:
            try:
                chat = await context.bot.get_chat(user_id)
                username = f"(@{chat.username})" if chat.username else ""
                stats_text += f"  - {escape_markdown(chat.full_name)} {username} ID: `{user_id}`\n"
            except Exception as e:
                stats_text += f"  - Could not fetch info for user ID: `{user_id}` (Error: {e})\n"

    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/broadcast <your message>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user_ids = DB_DATA.get('users', [])
    sent_count, failed_count = 0, 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
    await update.message.reply_text(f"📢 Broadcast finished.\nSent: {sent_count}\nFailed: {failed_count}")

# --- User Interaction Handlers ---
async def course_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data
    courses = DB_DATA.get('courses', {})

    if course_key in courses:
        course = courses[course_key]
        context.user_data['selected_course'] = course
        context.user_data['selected_course_key'] = course_key

        # Track Course Views
        stats = DB_DATA.setdefault('stats', {})
        course_views = stats.setdefault('course_views', {})
        course_views[course_key] = course_views.get(course_key, 0) + 1
        save_data_to_gist()

        if course.get('status') == 'coming_soon':
            keyboard = [[InlineKeyboardButton("⬅️ Back to Courses", callback_data="main_menu")]]
            await query.edit_message_text(text=f"*{escape_markdown(course['name'])}* is launching soon! Stay tuned.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
            return SELECTING_ACTION

        buttons = [
            [InlineKeyboardButton("💬 Talk to Admin", callback_data="talk_admin")],
            [InlineKeyboardButton("🛒 Buy Full Course", callback_data="buy_course")],
            [InlineKeyboardButton("⬅️ Back to Courses", callback_data="main_menu")],
        ]
        course_details = COURSE_DETAILS_TEXT.format(course_name=escape_markdown(course['name']))
        await query.edit_message_text(text=course_details, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN_V2)
    return SELECTING_ACTION

async def handle_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data
    course = context.user_data.get('selected_course')
    course_key = context.user_data.get('selected_course_key')

    if not course or not course_key:
        await query.edit_message_text("Something went wrong. Please /start over.")
        return ConversationHandler.END

    if action == "talk_admin":
        await query.edit_message_text(text="Please type your message to the admin and send it.")
        return FORWARD_TO_ADMIN
    
    elif action == "buy_course":
        keyboard = [
            [InlineKeyboardButton(f"💳 Pay ₹{course['price']} Now", url=RAZORPAY_LINK)],
            [InlineKeyboardButton("✅ Already Paid? Share Screenshot", callback_data="share_screenshot")],
            [InlineKeyboardButton("⬅️ Back", callback_data=course_key)] 
        ]
        buy_text = BUY_COURSE_TEXT.format(course_name=escape_markdown(course['name']), price=course['price'])
        await query.edit_message_text(text=buy_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
        return SELECTING_ACTION

    elif action == "share_screenshot":
        await query.edit_message_text(text="Please send the screenshot of your payment now.")
        return FORWARD_SCREENSHOT

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    course = context.user_data.get('selected_course', {'name': 'Not specified'})
    
    escaped_message = escape_markdown(update.message.text)
    forward_text = (
        f"📩 New message from {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"Regarding course: *{escape_markdown(course['name'])}*\n\n"
        f"Message:\n{escaped_message}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text("✅ Your message has been sent to the admin. They will reply to you here shortly.")
    return await main_menu_from_message(update, context)

async def forward_screenshot_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    course = context.user_data.get('selected_course', {'name': 'Not specified'})
    caption = (
        f"📸 New payment screenshot from: {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"For course: *{escape_markdown(course['name'])}*\n\n"
        f"Reply to this message to send the course link to the user\."
    )
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text("✅ Screenshot received! The admin will verify it and send you the course link here soon.")
    return await main_menu_from_message(update, context)

async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not update.message.reply_to_message:
        return
    
    original_msg = update.message.reply_to_message
    original_text = original_msg.text or original_msg.caption
    
    match = re.search(r'\(ID: `(\d+)`\)', original_text)
    if not match:
        await update.message.reply_text("❌ Could not find a user ID in the message you replied to. Please reply to the original forwarded message.")
        return
    
    user_id = int(match.group(1))
    reply_text = f"Admin replied:\n\n{update.message.text}"
    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text("✅ Reply sent successfully.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send message to user {user_id}. Error: {e}")

async def reply_by_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        user_id = int(context.args[0])
        message = " ".join(context.args[1:])
        if not message: raise ValueError("Empty message")
        
        reply_text = f"Admin replied:\n\n{message}"
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text(f"✅ Message sent to user ID `{user_id}`.")
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/reply <user_id> <message>`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send. Error: {e}")

async def handle_user_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_admin(update) or not update.message.reply_to_message or not update.message.reply_to_message.from_user.is_bot:
        return

    if "Admin replied:" in update.message.reply_to_message.text:
        forward_text = f"↪️ Follow-up from {escape_markdown(user.full_name)} \(ID: `{user.id}`\):\n\n{escape_markdown(update.message.text)}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
        await update.message.reply_text("✅ Your reply has been sent.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    error_message = f"🚨 Bot Error Alert 🚨\n\nAn error occurred: {context.error}"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=error_message)
    except Exception as e:
        logger.error(f"Failed to send error alert to admin: {e}")

def main() -> None:
    if not all([BOT_TOKEN, ADMIN_ID, GITHUB_USERNAME, GIST_ID, GITHUB_TOKEN]):
        logger.error("FATAL: One or more critical environment variables are missing.")
        return

    # Load initial data from Gist
    load_data_from_gist()

    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(main_menu, pattern="^main_menu$"),
                CallbackQueryHandler(handle_action, pattern="^(talk_admin|buy_course|share_screenshot)$"),
                # Regex to match anything that IS NOT one of the specific actions above.
                # This makes it work for any dynamically added course key.
                CallbackQueryHandler(course_selection_callback, pattern="^(?!main_menu$|talk_admin$|buy_course$|share_screenshot$).*$"),
            ],
            FORWARD_TO_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admin)],
            FORWARD_SCREENSHOT: [MessageHandler(filters.PHOTO, forward_screenshot_to_admin)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    
    # Admin Handlers
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("listcourses", list_courses))
    application.add_handler(CommandHandler("addcourse", add_course))
    application.add_handler(CommandHandler("editcourse", edit_course))
    application.add_handler(CommandHandler("delcourse", delete_course))
    application.add_handler(CommandHandler("set_order", set_course_order))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("reply", reply_by_id_command))

    # Reply Handlers
    application.add_handler(MessageHandler(filters.REPLY & filters.User(user_id=ADMIN_ID), reply_to_user))
    application.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND, handle_user_reply))
    
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
