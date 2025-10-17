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
    # You might want to exit the bot if the DB connection fails
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
# ... (Other bot texts like BUY_COURSE_TEXT, HELP_TEXT, ADMIN_HELP_TEXT remain the same) ...
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
    # Add user to DB if not already present
    users_collection.update_one({"_id": user.id}, {"$set": {"first_name": user.first_name, "last_name": user.last_name, "username": user.username}}, upsert=True)
    logger.info(f"User {user.first_name} ({user.id}) started the bot.")
    
    keyboard = []
    # Fetch courses sorted by 'order'
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

# ... (rest of the bot logic will be rewritten to use `courses_collection` and `users_collection`) ...
# This includes `course_selection_callback`, the new demo logic, and all admin commands.

# A simplified example of the new course_selection_callback
async def course_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data
    
    course = courses_collection.find_one({"_id": course_key})

    if course:
        context.user_data['selected_course'] = course

        buttons = [
            [InlineKeyboardButton("💬 Talk to Admin", callback_data=f"action_talk_admin_{course_key}")],
            [InlineKeyboardButton("🛒 Buy Full Course", callback_data=f"action_buy_{course_key}")],
        ]
        # Add demo button if demos exist
        if course.get("demo_lectures", {}).get("subjects"):
             buttons.insert(0, [InlineKeyboardButton("🎬 Watch Demo", callback_data=f"action_demo_{course_key}")])
        
        buttons.append([InlineKeyboardButton("⬅️ Back to Courses", callback_data="main_menu")])
        
        reply_markup = InlineKeyboardMarkup(buttons)
        course_details = COURSE_DETAILS_TEXT.format(course_name=escape_markdown(course['name']))
        await query.edit_message_text(text=course_details, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

    return SELECTING_ACTION

async def handle_demo_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1] # e.g., from "action_demo_rrb_ssc_je_madeeasy"
    
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
    # Fallback if no demos
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
    
    # Return to the subject selection menu
    return await handle_demo_selection(update, context)


async def add_demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    try:
        args_str = " ".join(context.args)
        course_key, subject_key, msg_id_str, button_text = [p.strip() for p in args_str.split(';')]
        msg_id = int(msg_id_str)
        
        # Using dot notation to update a nested document field in MongoDB
        update_field = f"demo_lectures.subjects.{subject_key}"
        result = courses_collection.update_one(
            {"_id": course_key},
            {"$set": {update_field: {"button_text": button_text, "message_id": msg_id}}}
        )
        
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Demo lecture added/updated for course `{course_key}`.")
        else:
            await update.message.reply_text(f"❌ Course with key `{course_key}` not found.")
            
    except Exception as e:
        logger.error(f"Error in adddemo: {e}")
        await update.message.reply_text("Usage: `/adddemo <course_key>; <subject_key>; <msg_id>; <button_text>`")


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
                CallbackQueryHandler(course_selection_callback, pattern="^(?!main_menu$|action_demo_).*$"), # Generic course selection
                # Add other action handlers here (buy, talk admin)
            ],
            SELECTING_DEMO_SUBJECT: [
                CallbackQueryHandler(send_demo_lecture, pattern="^demo_"),
                CallbackQueryHandler(course_selection_callback, pattern="^(?!demo_).*$"), # Back button
            ],
            # Add other states (FORWARD_TO_ADMIN, etc.)
        },
        fallbacks=[CommandHandler("start", start)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("adddemo", add_demo_command))
    # ... Add all other command handlers ...

    logger.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()

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
