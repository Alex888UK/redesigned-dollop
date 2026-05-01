import os
import re
import json
import logging
import asyncio
from pathlib import Path
from datetime import time
from openai import OpenAI
from telegram import Update, Bot
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
    JobQueue,
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID = os.environ["ASSISTANT_ID"]

# Channel to auto-post to (e.g. "@AtomyBGAkademia" or numeric chat ID)
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")

# Admin user IDs who can broadcast (comma-separated Telegram user IDs)
# Find your ID by messaging @userinfobot on Telegram
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

# Auto-post schedule: hours in UTC (e.g. "9,18" for 9am and 6pm UTC)
POST_HOURS = [int(x.strip()) for x in os.environ.get("POST_HOURS", "9,18").split(",") if x.strip()]

# File to persist user IDs (survives Railway restarts)
USERS_FILE = Path("users.json")

# ─── OpenAI CLIENT ────────────────────────────────────────────────────
client = OpenAI(api_key=OPENAI_API_KEY)
threads = {}

# ─── USER STORAGE ─────────────────────────────────────────────────────

def load_users() -> set:
    """Load saved user IDs from file."""
    if USERS_FILE.exists():
        try:
            data = json.loads(USERS_FILE.read_text())
            return set(data)
        except Exception:
            return set()
    return set()


def save_users(users: set):
    """Save user IDs to file."""
    USERS_FILE.write_text(json.dumps(list(users)))


# All known user IDs
known_users = load_users()


def register_user(user_id: int):
    """Add a user to the known users list."""
    if user_id not in known_users:
        known_users.add(user_id)
        save_users(known_users)
        logger.info(f"New user registered: {user_id} (total: {len(known_users)})")


# ─── OPENAI HELPERS ───────────────────────────────────────────────────

def clean_response(text: str) -> str:
    """Remove source annotation markers from the response."""
    cleaned = re.sub(r'【[^】]*】', '', text)
    cleaned = re.sub(r'  +', ' ', cleaned)
    return cleaned.strip()


def ask_assistant(thread_id: str, message: str) -> str:
    """Send a message to the assistant and get the response."""
    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=message,
    )

    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=ASSISTANT_ID,
        poll_interval_ms=1000,
    )

    if run.status == "completed":
        messages = client.beta.threads.messages.list(
            thread_id=thread_id, order="desc", limit=1
        )
        if messages.data:
            text = ""
            for block in messages.data[0].content:
                if block.type == "text":
                    text += block.text.value
            return clean_response(text) if text else "No response generated."

    logger.error(f"Run status: {run.status}")
    return "⚠️ Error generating response."


def generate_content(prompt: str) -> str:
    """Generate content using the assistant (creates a fresh thread)."""
    thread = client.beta.threads.create()
    return ask_assistant(thread.id, prompt)


# ─── BOT HANDLERS ────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id
    register_user(user_id)

    await update.message.reply_text(
        "👋 Здравейте! Аз съм AI асистент на Atomy BG Akademia.\n"
        "Мога да отговарям на въпроси за продуктите и бизнеса с Atomy.\n\n"
        "Просто ми изпратете съобщение!\n\n"
        "Команди:\n"
        "/clear - Нов разговор\n"
        "/start - Покажи това съобщение"
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command."""
    user_id = update.effective_user.id
    threads.pop(user_id, None)
    await update.message.reply_text("🗑 Разговорът е изчистен!")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages - send to assistant and reply."""
    user_id = update.effective_user.id
    user_message = update.message.text

    if not user_message:
        return

    # Register user for broadcasting
    register_user(user_id)

    # Send typing indicator
    await update.message.chat.send_action("typing")

    try:
        if user_id not in threads:
            thread = client.beta.threads.create()
            threads[user_id] = thread.id

        reply = ask_assistant(threads[user_id], user_message)

        if len(reply) <= 4096:
            await update.message.reply_text(reply)
        else:
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i + 4096])

    except Exception as e:
        logger.error(f"Error: {e}")
        threads.pop(user_id, None)
        await update.message.reply_text(f"⚠️ Error: {e}")


# ─── FEATURE 2: BROADCAST TO ALL USERS ───────────────────────────────

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command: /broadcast <message>
    Sends a message to all registered bot users.
    """
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized to broadcast.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n\n"
            "Example:\n"
            "/broadcast 🎉 Нови продукти в каталога! Пишете ми за повече информация."
        )
        return

    message = " ".join(context.args)
    sent = 0
    failed = 0

    await update.message.reply_text(f"📤 Broadcasting to {len(known_users)} users...")

    for uid in known_users.copy():
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            sent += 1
            # Small delay to avoid rate limits
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")
            failed += 1
            # Remove users who blocked the bot
            if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
                known_users.discard(uid)
                save_users(known_users)

    await update.message.reply_text(f"✅ Broadcast done: {sent} sent, {failed} failed.")


async def broadcast_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command: /broadcast_ai <prompt>
    Generates content with ChatGPT from your knowledge base, then broadcasts it.
    """
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /broadcast_ai <prompt>\n\n"
            "Example:\n"
            '/broadcast_ai Write a short tip about HemoHIM G benefits in Bulgarian'
        )
        return

    prompt = " ".join(context.args)

    await update.message.reply_text("🤖 Generating content...")

    content = generate_content(prompt)

    # Show preview first
    await update.message.reply_text(
        f"📝 Preview:\n\n{content}\n\n"
        f"Send /confirm_broadcast to send this to {len(known_users)} users, "
        f"or /cancel to cancel."
    )

    # Store pending broadcast
    context.user_data["pending_broadcast"] = content


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and send the pending AI broadcast."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        return

    content = context.user_data.get("pending_broadcast")
    if not content:
        await update.message.reply_text("No pending broadcast. Use /broadcast_ai first.")
        return

    sent = 0
    failed = 0

    await update.message.reply_text(f"📤 Sending to {len(known_users)} users...")

    for uid in known_users.copy():
        try:
            await context.bot.send_message(chat_id=uid, text=content)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")
            failed += 1
            if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
                known_users.discard(uid)
                save_users(known_users)

    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text(f"✅ Broadcast done: {sent} sent, {failed} failed.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel pending broadcast."""
    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text("❌ Broadcast cancelled.")


# ─── FEATURE 3: AUTO-POST TO CHANNEL ─────────────────────────────────

# Content prompts for auto-posting — customize these!
CONTENT_PROMPTS = [
    "Write a short engaging Telegram post in Bulgarian about one Atomy product benefit. "
    "Include an emoji. Keep it under 300 characters. Make it feel personal and helpful.",

    "Write a short motivational Telegram post in Bulgarian about the Atomy business opportunity. "
    "Include an emoji. Keep it under 300 characters.",

    "Write a short Telegram tip in Bulgarian about skincare or health using Atomy products. "
    "Include an emoji. Keep it under 300 characters.",

    "Write a short Telegram post in Bulgarian highlighting a specific Atomy product from the catalogue. "
    "Mention what problem it solves. Include an emoji. Keep it under 300 characters.",

    "Write a short Telegram post in Bulgarian about the Atomy compensation plan advantage. "
    "Keep it simple and motivational. Include an emoji. Keep it under 300 characters.",
]


async def auto_post_to_channel(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: generate content and post to channel."""
    if not CHANNEL_ID:
        logger.warning("CHANNEL_ID not set, skipping auto-post.")
        return

    import random
    prompt = random.choice(CONTENT_PROMPTS)

    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        logger.info(f"Auto-posted to channel: {content[:50]}...")
    except Exception as e:
        logger.error(f"Failed to auto-post: {e}")


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command: /post_now <optional prompt>
    Generate and post to channel immediately.
    """
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return

    if not CHANNEL_ID:
        await update.message.reply_text("⚠️ CHANNEL_ID is not set in environment variables.")
        return

    import random
    if context.args:
        prompt = " ".join(context.args)
    else:
        prompt = random.choice(CONTENT_PROMPTS)

    await update.message.reply_text("🤖 Generating and posting to channel...")

    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        await update.message.reply_text(f"✅ Posted to channel:\n\n{content}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed: {e}")


# ─── ADMIN INFO ───────────────────────────────────────────────────────

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /stats - show bot statistics."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        f"📊 Bot Stats:\n"
        f"• Registered users: {len(known_users)}\n"
        f"• Active threads: {len(threads)}\n"
        f"• Channel: {CHANNEL_ID or 'Not set'}\n"
        f"• Auto-post hours (UTC): {POST_HOURS}"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user their Telegram ID (useful for setting up ADMIN_IDS)."""
    await update.message.reply_text(f"Your Telegram ID: `{update.effective_user.id}`")


# ─── MAIN ─────────────────────────────────────────────────────────────

def main():
    """Start the bot."""
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("myid", myid))

    # Admin commands
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("broadcast_ai", broadcast_ai))
    app.add_handler(CommandHandler("confirm_broadcast", confirm_broadcast))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("post_now", post_now))
    app.add_handler(CommandHandler("stats", stats))

    # Message handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule auto-posts to channel
    if CHANNEL_ID and POST_HOURS:
        job_queue = app.job_queue
        for hour in POST_HOURS:
            job_queue.run_daily(
                auto_post_to_channel,
                time=time(hour=hour, minute=0),
                name=f"auto_post_{hour}",
            )
            logger.info(f"Scheduled auto-post at {hour}:00 UTC")

    logger.info("Bot is running!")
    logger.info(f"Admins: {ADMIN_IDS}")
    logger.info(f"Channel: {CHANNEL_ID or 'Not set'}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
