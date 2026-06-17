import os
import re
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

# ─── LOAD SYSTEM PROMPT ───────────────────────────────────────────────
SYSTEM_PROMPT = Path("system_prompt.txt").read_text(encoding="utf-8")
logger.info(f"System prompt loaded: {len(SYSTEM_PROMPT)} characters")

# ─── FOOTER ───────────────────────────────────────────────────────────
FIXED_HASHTAGS = "#АтомиПродукти #АтомиЗдраве"

ALLOWED_HASHTAGS = [
    "#КорейскаКозметика",
    "#АтомиКрасота",
    "#HemoHIM",
    "#КорейскиПродукти",
    "#АтомиКозметика",
    "#Здраве",
]

DISCLAIMER = (
    "⚠️ Продуктите на Atomy се продават САМО в официалните сайтове: "
    "🇪🇺 eu.atomy.com | 🇬🇧 uk.atomy.com. "
    "Atomy няма официален сайт за продажби в България. "
    "За контакти: https://atomybgakademia.org/contacts"
)

HASHTAG_INSTRUCTION = (
    "В самия край на поста добави САМО ЕДИН хаштаг от този списък "
    "(избери най-подходящия за темата на поста): "
    + " ".join(ALLOWED_HASHTAGS)
)

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

# ─── CONVERSATION HISTORY ─────────────────────────────────────────────
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

def clean_content(content: str) -> str:
    content = re.sub(r'\[\d+\]', '', content)
    content = re.sub(
        r'⚠️ Продуктите на Atomy.*?atomybgakademia\.org/contacts',
        '',
        content,
        flags=re.DOTALL
    )
    content = re.sub(r'#\S+', '', content)
    return content.strip()

def extract_hashtag(raw: str) -> str:
    for tag in ALLOWED_HASHTAGS:
        if tag.lower() in raw.lower():
            return tag
    return "#Здраве"

def build_footer(extra_tag: str) -> str:
    return f"\n\n{DISCLAIMER}\n\n{FIXED_HASHTAGS} {extra_tag}"

def generate_content(prompt: str) -> str:
    full_prompt = (
        f"{prompt}\n\n"
        f"СТРУКТУРА НА ПОСТА:\n"
        f"1. ПЪРВИ РЕД — кратък, закачлив хук (1 изречение, макс 10 думи). "
        f"Може да е въпрос, изненадващ факт или силно твърдение. Без емоджи на хука.\n"
        f"2. ОСНОВЕН ТЕКСТ — информация за продукта от системния промпт.\n"
        f"3. ПРИЗИВ ЗА ДЕЙСТВИЕ (CTA) — един ред в края.\n\n"
        f"ВАЖНО: {HASHTAG_INSTRUCTION}\n"
        f"НЕ добавяй disclaimer или контактна информация."
    )

    response = openai_client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": full_prompt},
        ],
        max_tokens=2000,
        temperature=0.3,
    )

    raw = response.choices[0].message.content
    extra_tag = extract_hashtag(raw)
    content = clean_content(raw)
    footer = build_footer(extra_tag)
    return content + footer

# ─── CONTENT PROMPTS ──────────────────────────────────────────────────
CONTENT_PROMPTS = [
    # ── ЗДРАВНИ ДОБАВКИ ──
    "Напиши пост за Telegram канал за HemoHIM G. "
    "Включи: цена 105.00 EUR, 73,000 PV, билките Angelica sinensis, Ligusticum chuanxiong, Paeonia lactiflora, разработен с KAERI. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Inner Collagen. "
    "Включи: цена 34.00 EUR, 19,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Alaska E-Omega 3. "
    "Включи: цена 22.50 EUR, 9,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Vitamin C. "
    "Включи: цена 22.50 EUR, 9,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Pure Spirulina 100%. "
    "Включи: цена 24.00 EUR, 10,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Eye Lutein. "
    "Включи: цена 32.00 EUR, 15,000 PV, нов продукт. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Noni Bottle. "
    "Включи: цена 49.00 EUR, 35,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Fermented Noni Drink Pouch. "
    "Включи: цена 56.50 EUR, 37,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Pomegranate Mixed Fruit Jelly. "
    "Включи: цена 67.50 EUR, 30,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за The Ultimate Set (HemoHIM G + Noni Pouch). "
    "Включи: цена 159.00 EUR, 150,000 PV, промо оферта. Използвай 1-2 емоджи.",

    # ── КОЗМЕТИКА ──
    "Напиши пост за Telegram канал за Absolute Ampoule. "
    "Включи: цена 41.00 EUR, 27,000 PV, от линията Absolute. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Absolute Nutrition Cream. "
    "Включи: цена 37.00 EUR, 23,000 PV, от линията Absolute. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Absolute 24K Gold Night Mask. "
    "Включи: цена 28.00 EUR, 21,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Evening Care 4 Set. "
    "Включи: цена 37.00 EUR, 14,000 PV, комплект за вечерна грижа 4 продукта. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Marine Ampoule Eye Patch. "
    "Включи: цена 19.50 EUR, 10,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Absolute Urban Shield Sun Cushion. "
    "Включи: цена 15.00 EUR, 9,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Absolute Essence Sunscreen. "
    "Включи: цена 12.50 EUR, 6,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Sun Stick. "
    "Включи: цена 12.00 EUR, 5,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Daily Expert Mask Moisturising. "
    "Включи: цена 12.50 EUR, 5,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Hand Cream. "
    "Включи: цена 15.50 EUR, 7,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Body Lotion. "
    "Включи: цена 12.00 EUR, 4,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Lip Glow. "
    "Включи: цена 12.00 EUR, 6,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Adelica Volume Mascara Black. "
    "Включи: цена 11.50 EUR, 6,250 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Foam Cleanser. "
    "Включи: цена 9.50 EUR, 3,500 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Deep Cleanser. "
    "Включи: цена 9.50 EUR, 3,500 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Foot Nourishing Cream. "
    "Включи: цена 12.00 EUR, 7,000 PV. Използвай 1-2 емоджи.",

    # ── ГРИЖА ЗА КОСА ──
    "Напиши пост за Telegram канал за Herbal Shampoo. "
    "Включи: цена 14.50 EUR, 7,000 PV, билков шампоан за здрав скалп. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Protein Intensive Shampoo. "
    "Включи: цена 20.00 EUR, 10,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Protein Intensive Treatment. "
    "Включи: цена 16.50 EUR, 9,500 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Scalpcare 2 Set. "
    "Включи: цена 28.00 EUR, 11,000 PV, комплект 2 продукта. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Hair Oil Complex. "
    "Включи: цена 13.50 EUR, 6,000 PV. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Saengmodan Hair Tonic. "
    "Включи: цена 14.00 EUR, 7,000 PV. Използвай 1-2 емоджи.",

    # ── ОРАЛНА ХИГИЕНА ──
    "Напиши пост за Telegram канал за Atomy Toothpaste 200g x5. "
    "Включи: цена 18.50 EUR, 4,000 PV, комплект 5 броя. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Oral Care System. "
    "Включи: цена 14.50 EUR, 7,000 PV. Използвай 1-2 емоджи.",

    # ── ЛИЧНА ГРИЖА И ДОМА ──
    "Напиши пост за Telegram канал за Sheet Laundry Detergent. "
    "Включи: цена 11.00 EUR, 6,500 PV, перилен препарат на листчета. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Cafe Arabica 50T. "
    "Включи: цена 12.50 EUR, 2,000 PV, премиум кафе 50 пакетчета. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Atomy Puer Tea. "
    "Включи: цена 25.00 EUR, 14,000 PV. Използвай 1-2 емоджи.",

    # ── ПРОМОЦИИ EU ──
    "Напиши пост за Telegram канал за Easter Promotion 1 — Triple Sun с Double PV. "
    "Включи: 3x Sun Stick + 3x Absolute Urban Shield Sun Cushion, цена 81.00 EUR, 84,000 PV (двоен PV). Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Easter Promotion 2 — Medicook 4SET. "
    "Включи: 9-частен комплект за готвене, цена 450.00 EUR (намалена от 550.00 EUR), 300,000 PV + 20,000 бонус, ограничено количество. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за THE FAME линия. "
    "Включи: цена 99.50 EUR, 60,000 PV. Използвай 1-2 емоджи.",

    # ── ПРОМОЦИИ UK ──
    "Напиши пост за Telegram канал за HemoHIM G Challenge UK. "
    "Включи: цена £93.00, 73,000 PV, безплатна доставка. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Best of Atomy Bundle 2026 UK. "
    "Включи: цена £385.00, 300,000 PV, безплатна доставка. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Mino Knives Set of 2 UK. "
    "Включи: цена £150.00, 70,000 PV, безплатна доставка, нов продукт, ръчно изработени японски ножове. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Organic Fermented Noni Pouch x4 UK. "
    "Включи: цена £180.00, 150,000 PV, безплатна доставка. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram канал за Synergy Ampoule Program Set of 3 UK. "
    "Включи: цена £260.00, 250,000 PV, безплатна доставка. Използвай 1-2 емоджи.",

    # ── БИЗНЕС ──
    "Напиши мотивационен пост за Telegram за бизнес възможността с Atomy. "
    "Включи: безплатна регистрация, без месечни такси, без задължителни покупки, бинарна структура. Използвай 1-2 емоджи.",

    "Напиши пост за Telegram за Atomy Mastership награди UK. "
    "Включи: Sharon-Rose/Star Master билети £600, Royal/Crown/Imperial Master билети £3,000. Използвай 1-2 емоджи.",

    "Напиши образователен пост за Telegram за принципа Masstige на Atomy. "
    "Включи: Mass + Prestige, абсолютно качество на абсолютна цена, основана 2009. Използвай 1-2 емоджи.",
]

# ─── BOT HANDLERS ─────────────────────────────────────────────────────

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
            f"Изпрати /confirm_broadcast за публикуване до {len(known_users)} потребители, или /cancel."
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
    context.user_data.pop("pending_post", None)
    await update.message.reply_text("❌ Отказано.")

# ─── CHANNEL POST ─────────────────────────────────────────────────────

async def auto_post_to_channel(context: ContextTypes.DEFAULT_TYPE):
    if not CHANNEL_ID:
        return
    import random
    prompt = random.choice(CONTENT_PROMPTS)
    try:
        content = generate_content(prompt)
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        logger.info(f"Auto-posted: {prompt[:60]}...")
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
        f"• Пост часове (UTC): {POST_HOURS}\n"
        f"• Брой промпти: {len(CONTENT_PROMPTS)}\n"
        f"• Чакащ пост: {'Да' if context.user_data.get('pending_post') else 'Не'}\n"
        f"• System prompt: {len(SYSTEM_PROMPT)} символа"
    )


async def post_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a post preview before publishing to the channel."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нямате достъп.")
        return

    if not CHANNEL_ID:
        await update.message.reply_text("⚠️ CHANNEL_ID не е зададен.")
        return

    import random
    prompt = " ".join(context.args) if context.args else random.choice(CONTENT_PROMPTS)

    await update.message.reply_text("🤖 Генериране на преглед...")

    try:
        content = generate_content(prompt)
        context.user_data["pending_post"] = content

        separator = "─" * 32
        await update.message.reply_text(
            f"👁 ПРЕГЛЕД НА ПОСТА:
"
            f"{separator}

"
            f"{content}

"
            f"{separator}
"
            f"📢 Канал: {CHANNEL_ID}

"
            f"✅ /confirm_post — публикувай в канала
"
            f"✏️ /post_preview <нов промпт> — генерирай нов
"
            f"❌ /cancel — откажи"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Грешка: {e}")


async def confirm_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publish the previewed post to the channel."""
    if update.effective_user.id not in ADMIN_IDS:
        return

    content = context.user_data.get("pending_post")
    if not content:
        await update.message.reply_text(
            "⚠️ Няма чакащ пост за публикуване.
"
            "Използвай /post_preview <промпт> за да генерираш нов."
        )
        return

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        context.user_data.pop("pending_post", None)
        await update.message.reply_text(f"✅ Публикувано успешно в {CHANNEL_ID}!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Грешка при публикуване: {e}")

async def post_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нямате достъп.")
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Използване: /post_link <продукт> <линк>\n"
            "Пример: /post_link HemoHIM G https://atomybgakademia.org/atomy"
        )
        return

    link = context.args[-1]
    product = " ".join(context.args[:-1])

    await update.message.reply_text(f"⏳ Четa страницата: {link}")

    # Fetch the page
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(link, headers={"User-Agent": "Mozilla/5.0"})
            html = resp.text

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        page_text = soup.get_text(separator="\n", strip=True)[:3000]

    except Exception as e:
        await update.message.reply_text(f"❌ Грешка при четене на страницата: {e}")
        return

    # Generate post ONLY from page content
    prompt = (
        f"Ти си маркетинг копирайтър за Atomy България.\n\n"
        f"Напиши кратък рекламен Telegram пост на български за продукта \"{product}\".\n"
        f"Използвай САМО информацията от тази страница — не добавяй нищо от себе си:\n\n"
        f"{page_text}\n\n"
        f"Изисквания:\n"
        f"- Максимум 300 знака основен текст\n"
        f"- Привлекателен, естествен тон\n"
        f"- 1-2 емоджи\n"
        f"- В края добави: 📖 Прочети повече: {link}\n"
        f"- БЕЗ хаштагове, БЕЗ disclaimer"
    )

    await update.message.reply_text("🤖 Генериране...")
    try:
        response = openai_client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.5,
        )
        raw = response.choices[0].message.content.strip()
        extra_tag = extract_hashtag(raw)
        clean = clean_content(raw)
        footer = build_footer(extra_tag)
        content = clean + footer

        await context.bot.send_message(chat_id=CHANNEL_ID, text=content)
        await update.message.reply_text(f"✅ Публикувано:\n\n{content}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Грешка: {e}")

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
    app.add_handler(CommandHandler("post_link", post_link))
    app.add_handler(CommandHandler("post_preview", post_preview))
    app.add_handler(CommandHandler("confirm_post", confirm_post))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if CHANNEL_ID and POST_HOURS:
        job_queue = app.job_queue
        if job_queue:
            for hour in POST_HOURS:
                job_queue.run_daily(auto_post_to_channel, time=time(hour=hour, minute=0))
                logger.info(f"Scheduled auto-post at {hour}:00 UTC")

    logger.info(f"Bot running | Model: {MODEL} | Admins: {ADMIN_IDS} | Prompts: {len(CONTENT_PROMPTS)}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
