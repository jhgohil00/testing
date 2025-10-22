import os
import logging
import threading
import json
import re
import requests
import pymongo
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
MONGO_DB_URL = os.environ.get("MONGO_DB_URL")
RAZORPAY_LINK = os.environ.get("RAZORPAY_LINK", "https://razorpay.me/@gateprep?amount=CVDUr6Uxp2FOGZGwAHntNg%3D%3D")

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Database Connection ---
try:
    client = pymongo.MongoClient(MONGO_DB_URL)
    db = client.get_default_database()
    courses_collection = db["courses"]
    users_collection = db["users"]
    logger.info("Successfully connected to MongoDB.")
except Exception as e:
    logger.error(f"FATAL: Could not connect to MongoDB: {e}")
    exit()

# --- Helper Functions ---
def escape_markdown(text: str) -> str:
    """Escapes special characters for Telegram MarkdownV2."""
    if not isinstance(text, str):
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# --- Bot Texts ---
COURSE_DETAILS_TEXT = """
📚 *Course Details: {course_name}*

Here's what you get:
\- Full Syllabus Coverage
\- 250\+ High\-Quality Video Lectures
\- Previous Year Questions \(PYQs\) Solved
\- Comprehensive Test Series
\- Regular Quizzes to Test Your Knowledge
\- Weekly Current Affairs Updates
\- Workbook & Study Materials
"""
BUY_COURSE_TEXT = """
✅ *You are about to purchase: {course_name}*

*Price: ₹{price}*

By purchasing, you will get full access to our private channel which includes:
\- Full syllabus lectures
\- 250\+ video lectures
\- Weekly current affairs
\- Workbook, Books, PYQs
\- Full Test Series

Please proceed with the payment\. If you have already paid, share the screenshot with us\.
"""
HELP_TEXT = """
👋 *Bot Help Guide*

Here's how to use me:

1️⃣ *Browse Courses*
\- Use the buttons on the main menu to see details about each course\.

2️⃣ *Talk to the Admin*
\- Select a course, then click *"💬 Talk to Admin"*
\- Type and send your message\. It will be forwarded to the admin\.
\- The admin's reply will be sent to you here\.

3️⃣ *Buy a Course*
\- After selecting a course, click *"🛒 Buy Full Course"*
\- Use the payment button to pay\.
\- After paying, click *"✅ Already Paid? Share Screenshot"* and send your payment screenshot\.

If you have any issues, feel free to use the "Talk to Admin" feature\.
"""
ADMIN_HELP_TEXT = """
👑 *Admin Panel Commands*

`/admin` \- Show this panel\.
`/listcourses` \- List all courses\.
`/addcourse <key>; <name>; <price>; <status>`
  _Ex: /addcourse new\_course; New Course; 199; available_
`/editcourse <key>; <name>; <price>; <status>`
  _Ex: /editcourse new\_course; "Adv Course"; 249; coming\_soon_
`/delcourse <key>` \- Remove a course\.
`/set_order <key> <order_num>` \- Change course display order\.
  _Ex: /set\_order new\_course 1_
`/adddemo <key>; <subject_key>; <msg_id>; <button_text>`
  _Ex: /adddemo new\_course; thermo; 123; Thermodynamics 🔥_
`/stats` \- View bot usage statistics and user list\.
`/broadcast <message>` \- Send a message to all users\.
`/reply <user_id> <message>` \- Send a direct message to a user\.

_Statuses: `available` or `coming_soon`_
"""
# --- Conversation States ---
SELECTING_ACTION, SELECTING_DEMO_SUBJECT, FORWARD_TO_ADMIN, FORWARD_SCREENSHOT = range(4)

# --- Command & Message Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    users_collection.update_one(
        {"_id": user.id}, 
        {"$set": {"first_name": user.first_name, "last_name": user.last_name, "username": user.username}}, 
        upsert=True
    )
    logger.info(f"User {user.first_name} ({user.id}) started the bot.")
    
    keyboard = []
    for course in courses_collection.find().sort("order", 1):
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=course['_id'])])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\nPlease select a course to view details or use /help for instructions.",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def main_menu_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for course in courses_collection.find().sort("order", 1):
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=course['_id'])])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Please select a course to view details:",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def main_menu_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = []
    for course in courses_collection.find().sort("order", 1):
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=course['_id'])])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "You can select another course:",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def course_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data
    
    course = courses_collection.find_one({"_id": course_key})

    if course:
        context.user_data['selected_course'] = course

        buttons = []
        if course.get("demo_lectures", {}).get("subjects"):
             buttons.append([InlineKeyboardButton("🎬 Watch Demo", callback_data=f"action_demo_{course_key}")])
        
        buttons.extend([
            [InlineKeyboardButton("💬 Talk to Admin", callback_data=f"action_talk_admin_{course_key}")],
            [InlineKeyboardButton("🛒 Buy Full Course", callback_data=f"action_buy_{course_key}")],
            [InlineKeyboardButton("⬅️ Back to Courses", callback_data="main_menu")]
        ])
        
        reply_markup = InlineKeyboardMarkup(buttons)
        course_details = COURSE_DETAILS_TEXT.format(course_name=escape_markdown(course['name']))
        await query.edit_message_text(text=course_details, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

    return SELECTING_ACTION

async def handle_demo_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1]
    
    course = courses_collection.find_one({"_id": course_key})
    if course and course.get("demo_lectures", {}).get("subjects"):
        subjects = course["demo_lectures"]["subjects"]
        keyboard = []
        for key, details in subjects.items():
            keyboard.append([InlineKeyboardButton(details["button_text"], callback_data=f"demo_{course_key}_{key}")])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=course_key)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Please select a subject to watch the demo lecture:", reply_markup=reply_markup)
        return SELECTING_DEMO_SUBJECT
    
    await query.edit_message_text("No demo lectures available for this course.")
    return SELECTING_ACTION

async def send_demo_lecture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Forwarding lecture, please wait...")
    
    _, course_key, subject_key = query.data.split('_')
    
    course = courses_collection.find_one({"_id": course_key})
    if course:
        demo_info = course["demo_lectures"]
        subject_info = demo_info["subjects"].get(subject_key)
        
        if subject_info:
            try:
                await context.bot.copy_message(
                    chat_id=query.from_user.id,
                    from_chat_id=demo_info["channel_id"],
                    message_id=subject_info["message_id"]
                )
            except Exception as e:
                logger.error(f"Failed to copy message: {e}")
                await query.message.reply_text("Sorry, there was an error fetching the lecture. Please try again later.")
    
    query.data = f"action_demo_{course_key}" # Hack to reuse the demo selection handler
    return await handle_demo_selection(update, context)

async def handle_talk_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please type your message to the admin and send it\.")
    return FORWARD_TO_ADMIN

async def handle_buy_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1]
    course = context.user_data.get('selected_course')
    
    if not course or course['_id'] != course_key:
        course = courses_collection.find_one({"_id": course_key})
        context.user_data['selected_course'] = course

    if not course:
        await query.edit_message_text("Error: Course not found. Please go /start")
        return SELECTING_ACTION

    keyboard = [
        [InlineKeyboardButton(f"💳 Pay ₹{course['price']} Now", url=RAZORPAY_LINK)],
        [InlineKeyboardButton("✅ Already Paid? Share Screenshot", callback_data=f"action_screenshot_{course_key}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=course_key)] 
    ]
    buy_text = BUY_COURSE_TEXT.format(course_name=escape_markdown(course['name']), price=course['price'])
    await query.edit_message_text(text=buy_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return SELECTING_ACTION

async def handle_share_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please send the screenshot of your payment now\.")
    return FORWARD_SCREENSHOT

async def forward_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    course = context.user_data.get('selected_course', {'name': 'Not specified'})
    
    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id

    escaped_message = escape_markdown(update.message.text)
    forward_text = (
        f"📩 New message from {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"Regarding course: *{escape_markdown(course['name'])}*\n\n"
        f"Message:\n{escaped_message}"
    )
    await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text("✅ Your message has been sent to the admin\. They will reply to you here shortly\.")
    return await main_menu_from_message(update, context)

async def forward_screenshot_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    course = context.user_data.get('selected_course', {'name': 'Not specified'})

    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id

    caption = (
        f"📸 New payment screenshot from: {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"For course: *{escape_markdown(course['name'])}*\n\n"
        f"Reply to this message to send the course link to the user\."
    )
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text("✅ Screenshot received\! The admin will verify it and send you the course link here soon\.")
    return await main_menu_from_message(update, context)

# --- Admin Handlers ---
def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    await update.message.reply_text(ADMIN_HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

async def list_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    courses = list(courses_collection.find().sort("order", 1))
    if not courses:
        await update.message.reply_text("No courses defined\. Use `/addcourse`\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    courses_info = "*📚 Current Courses:*\n\n"
    for course in courses:
        courses_info += (
            f"*Key:* `{course['_id']}`\n"
            f"*Name:* {escape_markdown(course['name'])}\n"
            f"*Price:* ₹{course['price']}\n"
            f"*Status:* {escape_markdown(course.get('status', 'N/A').replace('_', ' ').title())}\n"
            f"*Order:* {course.get('order', 'Not Set')}\n"
            f"\-\-\-\-\-\-\-\-\-\-\n"
        )
    await update.message.reply_text(courses_info, parse_mode=ParseMode.MARKDOWN_V2)

async def add_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        args_str = " ".join(context.args)
        key, name, price_str, status = [p.strip() for p in args_str.split(';')]
        status = status.lower()
        if status not in ["available", "coming_soon"]: raise ValueError("Invalid status")
        price = int(price_str)
        if price < 0: raise ValueError("Negative price")

        if courses_collection.find_one({"_id": key}):
             await update.message.reply_text(f"❌ Course with key `{key}` already exists\.", parse_mode=ParseMode.MARKDOWN_V2)
             return

        new_course = {
            "_id": key, "name": name, "price": price, "status": status,
            "order": courses_collection.count_documents({}) + 1,
            "demo_lectures": {"channel_id": None, "subjects": {}}
        }
        courses_collection.insert_one(new_course)
        await update.message.reply_text(f"✅ Course `{escape_markdown(name)}` \(key: `{key}`\) added\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in add_course: {e}")
        await update.message.reply_text("Usage: `/addcourse <key>; <name>; <price>; <status>`", parse_mode=ParseMode.MARKDOWN_V2)

async def edit_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        args_str = " ".join(context.args)
        key, new_name, new_price_str, new_status = [p.strip() for p in args_str.split(';')]
        new_status = new_status.lower()
        if new_status not in ["available", "coming_soon"]: raise ValueError("Invalid status")
        new_price = int(new_price_str)
        if new_price < 0: raise ValueError("Negative price")
        
        result = courses_collection.update_one(
            {"_id": key},
            {"$set": {"name": new_name, "price": new_price, "status": new_status}}
        )
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Course `{key}` updated\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{key}` not found\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error in edit_course: {e}")
        await update.message.reply_text("Usage: `/editcourse <key>; <new_name>; <new_price>; <new_status>`", parse_mode=ParseMode.MARKDOWN_V2)

async def delete_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        key = context.args[0]
        result = courses_collection.delete_one({"_id": key})
        if result.deleted_count > 0:
            await update.message.reply_text(f"✅ Course `{key}` deleted\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{key}` not found\.", parse_mode=ParseMode.MARKDOWN_V2)
    except IndexError:
        await update.message.reply_text("Usage: `/delcourse <key>`", parse_mode=ParseMode.MARKDOWN_V2)

async def set_course_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        key, order_str = context.args
        order = int(order_str)
        result = courses_collection.update_one({"_id": key}, {"$set": {"order": order}})
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Order for course `{key}` set to {order}\.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{key}` not found\.", parse_mode=ParseMode.MARKDOWN_V2)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/set_order <key> <order_number>`", parse_mode=ParseMode.MARKDOWN_V2)

async def add_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        args_str = " ".join(context.args)
        course_key, subject_key, msg_id_str, button_text = [p.strip() for p in args_str.split(';')]
        msg_id = int(msg_id_str)
        
        update_field = f"demo_lectures.subjects.{subject_key}"
        result = courses_collection.update_one(
            {"_id": course_key},
            {"$set": {update_field: {"button_text": button_text, "message_id": msg_id}}}
        )
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Demo lecture added/updated for course `{course_key}`.", parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(f"❌ Course with key `{course_key}` not found\.", parse_mode=ParseMode.MARKDOWN_V2)
            
    except Exception as e:
        logger.error(f"Error in adddemo: {e}")
        await update.message.reply_text("Usage: `/adddemo <course_key>; <subject_key>; <msg_id>; <button_text>`", parse_mode=ParseMode.MARKDOWN_V2)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    
    total_users = users_collection.count_documents({})
    stats_text = f"📊 *Bot Statistics*\n\n*Total Users:* `{total_users}`\n\n*User List:*\n"
    
    users = list(users_collection.find())
    if not users:
        stats_text += "  _No users have started the bot\._\n"
    else:
        for user in users:
            username = f"\(@{escape_markdown(user.get('username', ''))}\)" if user.get('username') else ""
            stats_text += f"  \- {escape_markdown(user.get('first_name', 'N/A'))} {username} ID: `{user['_id']}`\n"

    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/broadcast <your message>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    
    user_ids = [user["_id"] for user in users_collection.find({}, {"_id": 1})]
    sent_count, failed_count = 0, 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
    await update.message.reply_text(f"📢 Broadcast finished\.\nSent: {sent_count}\nFailed: {failed_count}", parse_mode=ParseMode.MARKDOWN_V2)

async def reply_by_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        user_id = int(context.args[0])
        message = " ".join(context.args[1:])
        if not message: raise ValueError("Empty message")
        
        reply_text = f"Admin replied:\n\n{message}"
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text(f"✅ Message sent to user ID `{user_id}`\.", parse_mode=ParseMode.MARKDOWN_V2)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/reply <user_id> <message>`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send\. Error: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not update.message.reply_to_message:
        return

    original_msg = update.message.reply_to_message
    original_text = original_msg.text or original_msg.caption
    user_id = None

    if original_text:
        match = re.search(r'\(ID: `(\d+)`\)', original_text)
        if match:
            user_id = int(match.group(1))

    if not user_id and original_msg.from_user.is_bot:
        last_user_id_key = f"last_chat_with_{ADMIN_ID}"
        if last_user_id_key in context.bot_data:
            user_id = context.bot_data[last_user_id_key]

    if not user_id:
        await update.message.reply_text(
            "❌ Could not determine which user to reply to\. Please reply to a message from a user, or use the `/reply <id> <msg>` command\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user_id

    reply_text = f"Admin replied:\n\n{update.message.text}"
    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text("✅ Reply sent successfully\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send message to user {user_id}\. Error: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

async def handle_user_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_admin(update) or not update.message.reply_to_message or not update.message.reply_to_message.from_user.is_bot:
        return

    if "Admin replied:" in update.message.reply_to_message.text:
        context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id

        forward_text = f"↪️ Follow\-up from {escape_markdown(user.full_name)} \(ID: `{user.id}`\):\n\n{escape_markdown(update.message.text)}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
        await update.message.reply_text("✅ Your reply has been sent\.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    error_message = f"🚨 Bot Error Alert 🚨\n\nAn error occurred: {context.error}"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=error_message)
    except Exception as e:
        logger.error(f"Failed to send error alert to admin: {e}")

# --- Main Application Setup ---
def main() -> None:
    if not all([BOT_TOKEN, ADMIN_ID, MONGO_DB_URL]):
        logger.error("FATAL: One or more critical environment variables are missing.")
        return

    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()

    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(main_menu_from_callback, pattern="^main_menu$"),
                CallbackQueryHandler(handle_demo_selection, pattern="^action_demo_"),
                CallbackQueryHandler(handle_talk_to_admin, pattern="^action_talk_admin_"),
                CallbackQueryHandler(handle_buy_course, pattern="^action_buy_"),
                CallbackQueryHandler(handle_share_screenshot, pattern="^action_screenshot_"),
                CallbackQueryHandler(course_selection_callback, pattern="^(?!main_menu$|action_demo_|action_talk_admin_|action_buy_|action_screenshot_).*$"),
            ],
            SELECTING_DEMO_SUBJECT: [
                CallbackQueryHandler(send_demo_lecture, pattern="^demo_"),
                CallbackQueryHandler(course_selection_callback, pattern="^(?!demo_).*$"),
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
    application.add_handler(CommandHandler("adddemo", add_demo_command))
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
