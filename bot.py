import os
import json
import logging
import asyncio
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
POST_HOURS = [int(x.strip()) for x in os.environ.get("POST_HOURS", "9,18").split(",") if x.strip()]
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
USERS_FILE = Path("users.json")

# ─── LOAD SYSTEM PROMPT FROM FILE ────────────────────────────────────
SYSTEM_PROMPT = Path("system_prompt.txt").read_text(encoding="utf-8")
logger.info(f"System prompt loaded: {len(SYSTEM_PROMPT)} characters")

# ─── OpenAI CLIENT ────────────────────────────────────────────────────
import httpx
from openai import OpenAI

openai_client = OpenAI(
    api_key=OPENAI_API_KEY,
    http_client=httpx.Client(timeout=60.0),
)

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

# ─── CONVERSATION HISTORY ────────────────────────────────────────────
conversations = {}
MAX_HISTORY = 20

def get_response(user_id: int, user_message: str) -> str:
    if user_id not in conversations:
        conversations[user_id] = []

    conversations[user_id].append({"role": "user", "content": user_message})

    if len(conversations[user_id]) > MAX_HISTORY:
        conversations[user_id] = conversations[user_id][-MAX_HISTORY:]

    response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *conversations[user_id],
        ],
        max_tokens=2000,
        temperature=0.3,
    )

    reply = response.choices[0].message.content
    conversations[user_id].append({"role": "assistant", "content": reply})
    return reply

def generate_content(prompt: str) -> str:
    response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2000,
        temperature=0.7,
    )
    return response.choices[0].message.content

# ─── BOT HANDLERS ────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user.id)
    await update.message.reply_text(
        "👋 Здравейте! Аз съм AI асистент на Atomy BG Akademia.\n"
        "Мога да отговарям на въпроси за продуктите и бизнеса с Atomy.\n\n"
        "Просто ми изпратете съобщение!\n\n"
        "Команди:\n"
        "/clear - Нов разговор\n"
        "/start - Покажи това съобщение"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conversations.pop(update.effective_user.id, None)
    await update.message.reply_text("🗑 Разговорът е изчистен!")

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    if not user_message:
        return

    register_user(user_id)
    await update.message.chat.send_action("typing")

    try:
        reply = get_response(user_id, user_message)
        if len(reply) <= 4096:
            await update.message.reply_text(reply)
        else:
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i + 4096])
    except Exception as e:
        logger.error(f"Error: {e}")
        conversations.pop(user_id, None)
        await update.message.reply_text(f"⚠️ Грешка: {type(e).__name__}: {str(e)[:200]}")

# ─── BROADCAST ────────────────────────────────────────────────────────

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нямате достъп.")
        return
    if not context.args:
        await update.message.reply_text("Използване: /broadcast <съобщение>")
        return

    message = " ".join(context.args)
    sent, failed = 0, 0
    await update.message.reply_text(f"📤 Изпращане до {len(known_users)} потребители...")

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

    await update.message.reply_text(f"✅ Готово: {sent} изпратени, {failed} неуспешни.")

async def broadcast_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нямате достъп.")
        return
    if not context.args:
        await update.message.reply_text("Използване: /broadcast_ai <промпт>")
        return

    prompt = " ".join(context.args)
    await update.message.reply_text("🤖 Генериране...")

    try:
        content = generate_content(prompt)
        await update.message.reply_text(
            f"📝 Преглед:\n\n{content}\n\n"
            f"/confirm_broadcast за изпращане до {len(known_users)} потребители, или /cancel."
        )
        context.user_data["pending_broadcast"] = content
    except Exception as e:
        await update.message.reply_text(f"⚠️ Грешка: {e}")

async def confirm_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    content = context.user_data.get("pending_broadcast")
    if not content:
        await update.message.reply_text("Няма чакащо съобщение.")
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
    await update.message.reply_text(f"✅ Готово: {sent} изпратени, {failed} неуспешни.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_broadcast", None)
    await update.message.reply_text("❌ Отказано.")

# ─── CHANNEL POST ─────────────────────────────────────────────────────

CONTENT_PROMPTS = [
    "Напиши пост за Telegram канал на български за един продукт на Atomy. "
    "Използвай емоджита. Напиши 150-200 думи. Следвай ръководството за публикации.",

    "Напиши мотивационен пост за Telegram на български за бизнес възможността с Atomy. "
    "Използвай емоджита. Напиши 150-200 думи. Следвай ръководството за публикации.",

    "Напиши съвет за Telegram на български за грижа за кожата или здраве с продукти на Atomy. "
    "Използвай емоджита. Напиши 150-200 думи. Следвай ръководството за публикации.",

    "Напиши пост за Telegram на български за актуална промоция на Atomy. "
    "Използвай емоджита. Напиши 150-200 думи. Следвай ръководството за публикации.",
]

async def auto_post_to_channel(context: ContextTypes.DEFAULT_TYPE):
    if not CHANNEL_ID:
        return
    import random
    prompt = random.choice(CONTENT_PROMPTS)
    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        logger.info("Auto-posted to channel")
    except Exception as e:
        logger.error(f"Auto-post failed: {e}")

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нямате достъп.")
        return
    if not CHANNEL_ID:
        await update.message.reply_text("⚠️ CHANNEL_ID не е зададен.")
        return

    import random
    prompt = " ".join(context.args) if context.args else random.choice(CONTENT_PROMPTS)
    await update.message.reply_text("🤖 Генериране и публикуване...")

    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        await update.message.reply_text(f"✅ Публикувано:\n\n{content}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Грешка: {e}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        f"📊 Статистика:\n"
        f"• Потребители: {len(known_users)}\n"
        f"• Модел: {MODEL}\n"
        f"• Канал: {CHANNEL_ID or 'Не е зададен'}\n"
        f"• System prompt: {len(SYSTEM_PROMPT)} символа"
    )

# ─── MAIN ─────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("broadcast_ai", broadcast_ai))
    app.add_handler(CommandHandler("confirm_broadcast", confirm_broadcast))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("post_now", post_now))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if CHANNEL_ID and POST_HOURS:
        job_queue = app.job_queue
        if job_queue:
            for hour in POST_HOURS:
                job_queue.run_daily(auto_post_to_channel, time=time(hour=hour, minute=0))
                logger.info(f"Scheduled auto-post at {hour}:00 UTC")

    logger.info(f"Bot running | Model: {MODEL} | Admins: {ADMIN_IDS}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
