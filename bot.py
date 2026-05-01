import os
import re
import json
import logging
import asyncio
import traceback
from pathlib import Path
from datetime import time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ASSISTANT_ID = os.environ.get("ASSISTANT_ID", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
POST_HOURS = [int(x.strip()) for x in os.environ.get("POST_HOURS", "9,18").split(",") if x.strip()]
USERS_FILE = Path("users.json")

# ─── OpenAI CLIENT (lazy init to catch errors) ───────────────────────
openai_client = None

def get_openai_client():
    global openai_client
    if openai_client is None:
        import httpx
        from openai import OpenAI
        # Create client with custom timeout and no proxy issues
        http_client = httpx.Client(timeout=60.0)
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            http_client=http_client,
        )
    return openai_client


# ─── USER STORAGE ─────────────────────────────────────────────────────
def load_users() -> set:
    if USERS_FILE.exists():
        try:
            return set(json.loads(USERS_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_users(users: set):
    USERS_FILE.write_text(json.dumps(list(users)))

known_users = load_users()

def register_user(user_id: int):
    if user_id not in known_users:
        known_users.add(user_id)
        save_users(known_users)
        logger.info(f"New user registered: {user_id} (total: {len(known_users)})")


# ─── OPENAI HELPERS ───────────────────────────────────────────────────
threads = {}

def clean_response(text: str) -> str:
    cleaned = re.sub(r'【[^】]*】', '', text)
    cleaned = re.sub(r'  +', ' ', cleaned)
    return cleaned.strip()


def ask_assistant(thread_id: str, message: str) -> str:
    client = get_openai_client()
    client.beta.threads.messages.create(
        thread_id=thread_id, role="user", content=message,
    )
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread_id, assistant_id=ASSISTANT_ID, poll_interval_ms=1000,
    )
    if run.status == "completed":
        messages = client.beta.threads.messages.list(
            thread_id=thread_id, order="desc", limit=1,
        )
        if messages.data:
            text = ""
            for block in messages.data[0].content:
                if block.type == "text":
                    text += block.text.value
            return clean_response(text) if text else "No response."
    return f"Run status: {run.status}"


def generate_content(prompt: str) -> str:
    client = get_openai_client()
    thread = client.beta.threads.create()
    return ask_assistant(thread.id, prompt)


# ─── BOT HANDLERS ────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user_id = update.effective_user.id
    threads.pop(user_id, None)
    await update.message.reply_text("🗑 Разговорът е изчистен!")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


# ─── TEST COMMANDS ────────────────────────────────────────────────────

async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test 1: Basic ChatGPT call (no assistant)."""
    await update.message.reply_text("🔄 Testing basic OpenAI API...")
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "Say hello in 5 words"}],
            max_tokens=20,
        )
        reply = response.choices[0].message.content
        await update.message.reply_text(f"✅ API works!\nResponse: {reply}")
    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error(f"test_api failed: {error_detail}")
        msg = f"❌ API failed!\n\nError type: {type(e).__name__}\nError: {str(e)[:500]}"
        await update.message.reply_text(msg)


async def test_assistant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test 2: Assistant API call."""
    if not ASSISTANT_ID:
        await update.message.reply_text("❌ ASSISTANT_ID not set!")
        return

    await update.message.reply_text(f"🔄 Testing Assistant {ASSISTANT_ID[:20]}...")
    try:
        client = get_openai_client()
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id, role="user", content="Hello, who are you?",
        )
        run = client.beta.threads.runs.create_and_poll(
            thread_id=thread.id, assistant_id=ASSISTANT_ID, poll_interval_ms=1000,
        )
        if run.status == "completed":
            messages = client.beta.threads.messages.list(
                thread_id=thread.id, order="desc", limit=1,
            )
            text = messages.data[0].content[0].text.value if messages.data else "No response"
            await update.message.reply_text(f"✅ Assistant works!\n\n{clean_response(text)[:500]}")
        else:
            await update.message.reply_text(f"❌ Run status: {run.status}\nError: {run.last_error}")
    except Exception as e:
        error_detail = traceback.format_exc()
        logger.error(f"test_assistant failed: {error_detail}")
        msg = f"❌ Assistant failed!\n\nError type: {type(e).__name__}\nError: {str(e)[:500]}"
        await update.message.reply_text(msg)


async def test_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test 3: Raw HTTP connection to OpenAI."""
    await update.message.reply_text("🔄 Testing raw connection...")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            )
            await update.message.reply_text(
                f"✅ Connection OK!\nStatus: {r.status_code}\nResponse: {r.text[:300]}"
            )
    except Exception as e:
        msg = f"❌ Connection failed!\n\nError type: {type(e).__name__}\nError: {str(e)[:500]}"
        await update.message.reply_text(msg)


async def debug_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show config info for debugging."""
    key_preview = OPENAI_API_KEY[:8] + "..." if OPENAI_API_KEY else "NOT SET"
    await update.message.reply_text(
        f"🔧 Debug Info:\n"
        f"• API Key: {key_preview}\n"
        f"• Assistant ID: {ASSISTANT_ID[:20] + '...' if ASSISTANT_ID else 'NOT SET'}\n"
        f"• Channel: {CHANNEL_ID or 'NOT SET'}\n"
        f"• Admin IDs: {ADMIN_IDS}\n"
        f"• Your ID: {update.effective_user.id}\n"
        f"• Users registered: {len(known_users)}\n"
        f"• POST_HOURS: {POST_HOURS}"
    )


# ─── MESSAGE HANDLER ─────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    if not user_message:
        return

    register_user(user_id)
    await update.message.chat.send_action("typing")

    try:
        client = get_openai_client()

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
        error_detail = traceback.format_exc()
        logger.error(f"Message handler error: {error_detail}")
        threads.pop(user_id, None)
        await update.message.reply_text(f"⚠️ Error: {type(e).__name__}: {str(e)[:300]}")


# ─── BROADCAST ────────────────────────────────────────────────────────

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    message = " ".join(context.args)
    sent, failed = 0, 0
    await update.message.reply_text(f"📤 Broadcasting to {len(known_users)} users...")

    for uid in known_users.copy():
        try:
            await context.bot.send_message(chat_id=uid, text=message)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                known_users.discard(uid)
                save_users(known_users)

    await update.message.reply_text(f"✅ Done: {sent} sent, {failed} failed.")


async def broadcast_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast_ai <prompt>")
        return

    prompt = " ".join(context.args)
    await update.message.reply_text("🤖 Generating content...")

    try:
        content = generate_content(prompt)
        await update.message.reply_text(
            f"📝 Preview:\n\n{content}\n\n"
            f"Send /confirm_broadcast to send to {len(known_users)} users, or /cancel."
        )
        context.user_data["pending_broadcast"] = content
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {type(e).__name__}: {str(e)[:300]}")


async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    content = context.user_data.get("pending_broadcast")
    if not content:
        await update.message.reply_text("No pending broadcast.")
        return

    sent, failed = 0, 0
    for uid in known_users.copy():
        try:
            await context.bot.send_message(chat_id=uid, text=content)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text(f"✅ Done: {sent} sent, {failed} failed.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text("❌ Cancelled.")


# ─── CHANNEL POST ─────────────────────────────────────────────────────

CONTENT_PROMPTS = [
    "Write an engaging Telegram post in Bulgarian about one Atomy product benefit. "
    "Include emojis. Write 150-200 words. Follow the posting guide document.",

    "Write a motivational Telegram post in Bulgarian about the Atomy business opportunity. "
    "Include emojis. Write 150-200 words. Follow the posting guide document.",

    "Write a Telegram tip in Bulgarian about skincare or health using Atomy products. "
    "Include emojis. Write 150-200 words. Follow the posting guide document.",
]


async def auto_post_to_channel(context: ContextTypes.DEFAULT_TYPE):
    if not CHANNEL_ID:
        return
    import random
    prompt = random.choice(CONTENT_PROMPTS)
    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        logger.info(f"Auto-posted to channel")
    except Exception as e:
        logger.error(f"Auto-post failed: {e}")


async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorized.")
        return
    if not CHANNEL_ID:
        await update.message.reply_text("⚠️ CHANNEL_ID not set.")
        return

    import random
    prompt = " ".join(context.args) if context.args else random.choice(CONTENT_PROMPTS)
    await update.message.reply_text("🤖 Generating and posting...")

    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        await update.message.reply_text(f"✅ Posted:\n\n{content}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {type(e).__name__}: {str(e)[:300]}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        f"📊 Stats:\n"
        f"• Users: {len(known_users)}\n"
        f"• Threads: {len(threads)}\n"
        f"• Channel: {CHANNEL_ID or 'Not set'}"
    )


# ─── MAIN ─────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("test_api", test_api))
    app.add_handler(CommandHandler("test_assistant", test_assistant))
    app.add_handler(CommandHandler("test_connection", test_connection))
    app.add_handler(CommandHandler("debug_info", debug_info))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("broadcast_ai", broadcast_ai))
    app.add_handler(CommandHandler("confirm_broadcast", confirm_broadcast))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("post_now", post_now))
    app.add_handler(CommandHandler("stats", stats))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduled posts
    if CHANNEL_ID and POST_HOURS:
        job_queue = app.job_queue
        if job_queue:
            for hour in POST_HOURS:
                job_queue.run_daily(
                    auto_post_to_channel,
                    time=time(hour=hour, minute=0),
                    name=f"auto_post_{hour}",
                )
                logger.info(f"Scheduled auto-post at {hour}:00 UTC")
        else:
            logger.warning("JobQueue not available - auto-posting disabled")

    logger.info("Bot is running!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
