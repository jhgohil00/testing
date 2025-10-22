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
from telegram.error import TelegramError

# --- Web Server ---
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

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load Course Data ---
COURSES_DATA = {}
try:
    with open('courses.json', 'r', encoding='utf-8') as f:
        COURSES_DATA = json.load(f)
    logger.info(f"Successfully loaded {len(COURSES_DATA)} courses from courses.json.")
except FileNotFoundError:
    logger.error("FATAL: courses.json not found.")
    exit()
except json.JSONDecodeError as e:
    logger.error(f"FATAL: courses.json contains invalid JSON: {e}")
    exit()

# --- Database Connection ---
try:
    client = pymongo.MongoClient(MONGO_DB_URL)
    db = client.get_default_database()
    users_collection = db["users"]
    logger.info("Successfully connected to MongoDB for user data.")
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
`/stats` \- View bot usage statistics and user list\.
`/broadcast <message>` \- Send a message to all users\.
`/reply <user_id> <message>` \- Send a direct message to a user\.

*To add, edit, or remove courses, please edit the `courses.json` file in the GitHub repository and commit your changes.*
"""
# --- Conversation States ---
SELECTING_ACTION, SELECTING_DEMO_SUBJECT, FORWARD_TO_ADMIN, FORWARD_SCREENSHOT = map(chr, range(4)) # Use characters for states

# --- Command & Message Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user = update.effective_user
    users_collection.update_one(
        {"_id": user.id},
        {"$set": {"first_name": user.first_name, "last_name": user.last_name, "username": user.username}},
        upsert=True
    )
    logger.info(f"User {user.first_name} ({user.id}) started the bot.")

    keyboard = []
    sorted_courses = sorted(COURSES_DATA.items(), key=lambda item: item[1].get('order', 999))
    for course_key, course in sorted_courses:
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=course_key)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    # Use reply_text for /start, edit_message_text otherwise
    if update.callback_query:
        await update.callback_query.edit_message_text(
             "Please select a course to view details:",
             reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            f"👋 Welcome, {user.first_name}!\n\nPlease select a course to view details or use /help for instructions.",
            reply_markup=reply_markup
        )
    return SELECTING_ACTION

async def main_menu_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    keyboard = []
    sorted_courses = sorted(COURSES_DATA.items(), key=lambda item: item[1].get('order', 999))
    for course_key, course in sorted_courses:
        button_text = f"{course['name']} - ₹{course['price']}"
        if course.get('status') == 'coming_soon':
            button_text += " (Coming Soon)"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=course_key)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "You can select another course:",
        reply_markup=reply_markup
    )
    return SELECTING_ACTION

async def course_details_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer()
    course_key = query.data

    course = COURSES_DATA.get(course_key)

    if course:
        context.user_data['selected_course_key'] = course_key # Store key for potential use

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
        return SELECTING_ACTION # Stay in the main action selection state
    else:
        await query.edit_message_text("Sorry, this course seems to be missing. Please select another.")
        # Trigger the main menu display again
        query.data = "main_menu"
        return await start(update, context)


async def demo_subject_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer()
    # Extract course key reliably, whether coming from course details or back button
    if query.data.startswith("action_demo_"):
        course_key = query.data.split('_')[-1]
    else: # Coming back from sending a demo
        course_key = context.user_data.get('selected_course_key')

    if not course_key:
        await query.edit_message_text("Error: Could not determine the course. Please start again.")
        query.data = "main_menu"
        return await start(update, context)

    context.user_data['selected_course_key'] = course_key # Ensure it's stored
    course = COURSES_DATA.get(course_key)

    if course and course.get("demo_lectures", {}).get("subjects"):
        subjects = course["demo_lectures"]["subjects"]
        keyboard = []
        for subject_key, details in subjects.items():
            keyboard.append([InlineKeyboardButton(details["button_text"], callback_data=f"demo_{course_key}_{subject_key}")])
        # Back button goes back to course details menu
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=course_key)])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Please select a subject to watch the demo lecture:", reply_markup=reply_markup)
        return SELECTING_DEMO_SUBJECT # Move to demo subject selection state
    else:
        await query.edit_message_text("No demo lectures available for this course.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=course_key)]]))
        return SELECTING_ACTION # Go back to course details


async def send_demo_lecture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer("Forwarding lecture...")

    _, course_key, subject_key = query.data.split('_')
    context.user_data['selected_course_key'] = course_key # Store for back button context

    course = COURSES_DATA.get(course_key)
    if course:
        demo_info = course.get("demo_lectures", {})
        subject_info = demo_info.get("subjects", {}).get(subject_key)

        if subject_info and demo_info.get("channel_id"):
            channel_id = demo_info["channel_id"]
            message_id = subject_info["message_id"]
            logger.info(f"Attempting to copy msg {message_id} from chat {channel_id} for user {query.from_user.id}")
            try:
                await context.bot.copy_message(
                    chat_id=query.from_user.id,
                    from_chat_id=channel_id,
                    message_id=message_id
                )
                logger.info("Copy successful.")
                # Don't edit the message here, let the user view the video
            except TelegramError as e:
                logger.error(f"TelegramError copying message: {e}")
                # Check for specific errors if needed (e.g., bot not admin, message not found)
                if "bot is not a member" in str(e):
                     await query.message.reply_text("Error: The bot needs to be an admin in the source channel.")
                     logger.error("BOT IS NOT ADMIN IN SOURCE CHANNEL.")
                elif "message to copy not found" in str(e):
                     await query.message.reply_text("Error: The demo video message could not be found.")
                     logger.error(f"MESSAGE ID {message_id} NOT FOUND IN CHANNEL {channel_id}.")
                else:
                    await query.message.reply_text("Sorry, there was an error fetching the lecture.")
            except Exception as e:
                logger.error(f"Generic error copying message: {e}")
                await query.message.reply_text("Sorry, an unexpected error occurred.")
        else:
             logger.warning(f"Demo info missing for course {course_key}, subject {subject_key}")
             await query.message.reply_text("Sorry, the lecture information is missing.")

    # Stay in the SELECTING_DEMO_SUBJECT state so the back button works correctly
    return SELECTING_DEMO_SUBJECT


async def buy_course_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1]
    context.user_data['selected_course_key'] = course_key # Store for context

    course = COURSES_DATA.get(course_key)

    if not course:
        await query.edit_message_text("Error: Course not found. Please start again.")
        query.data = "main_menu"
        return await start(update, context)

    keyboard = [
        [InlineKeyboardButton(f"💳 Pay ₹{course['price']} Now", url=RAZORPAY_LINK)],
        [InlineKeyboardButton("✅ Already Paid? Share Screenshot", callback_data=f"action_screenshot_{course_key}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=course_key)] # Back goes to course details
    ]
    buy_text = BUY_COURSE_TEXT.format(course_name=escape_markdown(course['name']), price=course['price'])
    await query.edit_message_text(text=buy_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)
    return SELECTING_ACTION # Stay in action selection

async def prompt_for_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1]
    context.user_data['selected_course_key'] = course_key # Store context
    await query.edit_message_text(text="Please send the screenshot of your payment now\.")
    return FORWARD_SCREENSHOT # Move to screenshot state

async def prompt_for_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    query = update.callback_query
    await query.answer()
    course_key = query.data.split('_')[-1]
    context.user_data['selected_course_key'] = course_key # Store context
    await query.edit_message_text(text="Please type your message to the admin and send it\.")
    return FORWARD_TO_ADMIN # Move to admin message state


async def forward_to_admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user = update.effective_user
    course_key = context.user_data.get('selected_course_key')
    course_name = COURSES_DATA.get(course_key, {}).get('name', 'Not specified')

    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id

    escaped_message = escape_markdown(update.message.text)
    forward_text = (
        f"📩 New message from {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"Regarding course: *{escape_markdown(course_name)}*\n\n"
        f"Message:\n{escaped_message}"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
        await update.message.reply_text("✅ Your message has been sent to the admin\. They will reply to you here shortly\.")
    except Exception as e:
        logger.error(f"Failed to forward message to admin: {e}")
        await update.message.reply_text("❌ Sorry, there was an error sending your message.")

    return await main_menu_from_message(update, context) # Show main menu buttons again

async def forward_screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    user = update.effective_user
    course_key = context.user_data.get('selected_course_key')
    course_name = COURSES_DATA.get(course_key, {}).get('name', 'Not specified')


    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id

    caption = (
        f"📸 New payment screenshot from: {escape_markdown(user.full_name)} \(ID: `{user.id}`\)\n"
        f"For course: *{escape_markdown(course_name)}*\n\n"
        f"Reply to this message to send the course link to the user\."
    )
    try:
        if update.message.photo:
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=update.message.photo[-1].file_id, caption=caption, parse_mode=ParseMode.MARKDOWN_V2)
            await update.message.reply_text("✅ Screenshot received\! The admin will verify it and send you the course link here soon\.")
        else:
            await update.message.reply_text("Please send a photo screenshot.")
            return FORWARD_SCREENSHOT # Stay in this state if it wasn't a photo
    except Exception as e:
        logger.error(f"Failed to forward screenshot to admin: {e}")
        await update.message.reply_text("❌ Sorry, there was an error sending your screenshot.")

    return await main_menu_from_message(update, context) # Show main menu buttons again

# --- Admin Handlers ---
def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    await update.message.reply_text(ADMIN_HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return

    total_users = users_collection.count_documents({})
    stats_text = f"📊 *Bot Statistics*\n\n*Total Users:* `{total_users}`\n\n*User List:*\n"

    users = list(users_collection.find().limit(200)) # Limit to avoid overly long messages
    if not users:
        stats_text += "  _No users have started the bot\._\n"
    else:
        for user in users:
            username = f"\(@{escape_markdown(user.get('username', ''))}\)" if user.get('username') else ""
            stats_text += f"  \- {escape_markdown(user.get('first_name', 'N/A'))} {username} ID: `{user['_id']}`\n"
        if len(users) == 200:
            stats_text += "\n_Note: User list truncated at 200 entries\._"


    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN_V2)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: `/broadcast <your message>`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    user_ids = [user["_id"] for user in users_collection.find({}, {"_id": 1})]
    sent_count, failed_count = 0, 0
    await update.message.reply_text(f"Starting broadcast to {len(user_ids)} users...")
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=int(user_id), text=message)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.warning(f"Failed to send broadcast to {user_id}: {e}")
    await update.message.reply_text(f"📢 Broadcast finished\.\nSent: {sent_count}\nFailed: {failed_count}", parse_mode=ParseMode.MARKDOWN_V2)

async def reply_by_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update): return
    try:
        user_id = int(context.args[0])
        message = " ".join(context.args[1:])
        if not message: raise ValueError("Empty message")

        reply_text = f"Admin replied:\n\n{message}"
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user_id # Set context for potential replies
        await update.message.reply_text(f"✅ Message sent to user ID `{user_id}`\.", parse_mode=ParseMode.MARKDOWN_V2)
    except (IndexError, ValueError):
        await update.message.reply_text("Usage: `/reply <user_id> <message>`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send\. Error: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

async def reply_to_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update) or not update.message.reply_to_message:
        return

    original_msg = update.message.reply_to_message
    original_text = original_msg.text or original_msg.caption
    user_id = None

    # Try finding ID in the replied message text
    if original_text:
        match = re.search(r'\(ID: `(\d+)`\)', original_text)
        if match:
            user_id = int(match.group(1))

    # If not found, check bot_data context if replying to bot's own message
    if not user_id and original_msg.from_user.is_bot:
        last_user_id_key = f"last_chat_with_{ADMIN_ID}"
        if last_user_id_key in context.bot_data:
            user_id = context.bot_data[last_user_id_key]

    if not user_id:
        await update.message.reply_text(
            "❌ Could not determine which user to reply to\. Please reply directly to a message *from* the user \(containing their ID\) or use the `/reply <id> <msg>` command\.",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user_id # Update context

    reply_text = f"Admin replied:\n\n{update.message.text}"
    try:
        await context.bot.send_message(chat_id=user_id, text=reply_text)
        await update.message.reply_text("✅ Reply sent successfully\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send message to user {user_id}\. Error: {escape_markdown(str(e))}", parse_mode=ParseMode.MARKDOWN_V2)

async def handle_user_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_admin(update) or not update.message.reply_to_message or not update.message.reply_to_message.from_user.is_bot:
        return # Ignore admin replies to self, non-replies, replies to users

    if "Admin replied:" in update.message.reply_to_message.text:
        context.bot_data[f"last_chat_with_{ADMIN_ID}"] = user.id # Store context

        forward_text = f"↪️ Follow\-up from {escape_markdown(user.full_name)} \(ID: `{user.id}`\):\n\n{escape_markdown(update.message.text)}"
        await context.bot.send_message(chat_id=ADMIN_ID, text=forward_text, parse_mode=ParseMode.MARKDOWN_V2)
        await update.message.reply_text("✅ Your reply has been sent\.")
    # else: ignore user replies to messages other than the admin's reply

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {update}", exc_info=context.error)
    # Don't send full error message to admin
    error_message = f"🚨 Bot Error Alert 🚨\n\nAn error occurred. Check the logs."
    try:
        if isinstance(context.error, TelegramError):
             error_message += f"\nError type: {type(context.error).__name__}"
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
                CallbackQueryHandler(start, pattern="^main_menu$"), # Use start for main menu
                CallbackQueryHandler(demo_subject_menu, pattern="^action_demo_"),
                CallbackQueryHandler(prompt_for_admin_message, pattern="^action_talk_admin_"),
                CallbackQueryHandler(buy_course_menu, pattern="^action_buy_"),
                CallbackQueryHandler(prompt_for_screenshot, pattern="^action_screenshot_"),
                # Handles course selection & Back button from Buy Course/Demo Subject
                CallbackQueryHandler(course_details_menu, pattern="^(?!main_menu$|action_demo_|action_talk_admin_|action_buy_|action_screenshot_|demo_).*$"),
            ],
            SELECTING_DEMO_SUBJECT: [
                CallbackQueryHandler(send_demo_lecture, pattern="^demo_"),
                # Back button goes back to course details
                CallbackQueryHandler(course_details_menu, pattern="^(?!demo_).*$"),
            ],
            FORWARD_TO_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admin_handler)],
            FORWARD_SCREENSHOT: [MessageHandler(filters.PHOTO, forward_screenshot_handler)],
        },
        fallbacks=[CommandHandler("start", start)], # Go back to start on any unknown command/message in conversation
        per_message=False # Important for callback query handling
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    # Admin Handlers (outside conversation)
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("reply", reply_by_id_command))

    # Reply Handlers (outside conversation)
    application.add_handler(MessageHandler(filters.REPLY & filters.User(user_id=ADMIN_ID), reply_to_user_handler))
    application.add_handler(MessageHandler(filters.REPLY & ~filters.COMMAND & ~filters.User(user_id=ADMIN_ID), handle_user_reply))

    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
