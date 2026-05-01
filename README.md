# Atomy Telegram Bot — ChatGPT + Broadcast + Auto-Post

A Telegram bot that answers questions from your knowledge base, broadcasts messages to all users, and auto-posts AI-generated content to a channel.

## Features

- **AI Q&A**: Users message the bot, it answers from your uploaded documents
- **Broadcast**: Send a message to all bot users with one command
- **AI Broadcast**: Generate content with ChatGPT and broadcast it (with preview)
- **Auto-post to channel**: Scheduled AI-generated posts to your Telegram channel
- **Manual post**: Instantly generate and post to channel on demand

## Setup

### Step 1: Create Telegram Bot
1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the **API token**

### Step 2: Create a Telegram Channel
1. Create a channel in Telegram (e.g. `@AtomyBGAkademia`)
2. Add your bot as an **administrator** to the channel
3. The bot needs permission to **post messages**

### Step 3: Get Your Telegram User ID
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It replies with your user ID (a number like `123456789`)
3. This is needed for the ADMIN_IDS variable

### Step 4: Set Up OpenAI Assistant
1. Go to [platform.openai.com/assistants](https://platform.openai.com/assistants)
2. Click **Create**, name it (e.g. "Atomy BG Assistant")
3. Set instructions, e.g.:
   ```
   You are a helpful assistant for Atomy BG Akademia.
   Answer questions about Atomy products, the compensation plan,
   and business opportunity based on the uploaded documents.
   Answer in Bulgarian unless the user writes in English.
   Be friendly, professional, and concise.
   ```
4. Enable **File search** → upload your Atomy catalogue PDFs, product guides, etc.
5. Save and copy the **Assistant ID** (`asst_...`)

### Step 5: Deploy on Railway
1. Push files to GitHub: `bot.py`, `requirements.txt`, `Procfile`, `.python-version`
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add these **Variables**:

| Variable | Example | Required |
|---|---|---|
| `TELEGRAM_TOKEN` | `7123456:AAH...` | Yes |
| `OPENAI_API_KEY` | `sk-...` | Yes |
| `ASSISTANT_ID` | `asst_abc123...` | Yes |
| `CHANNEL_ID` | `@AtomyBGAkademia` | For auto-posting |
| `ADMIN_IDS` | `123456789,987654321` | For broadcast |
| `POST_HOURS` | `9,18` | Optional (default: 9,18 UTC) |

## Admin Commands

Only users listed in `ADMIN_IDS` can use these:

| Command | What it does |
|---|---|
| `/broadcast Hello everyone!` | Sends "Hello everyone!" to all bot users |
| `/broadcast_ai Write a tip about HemoHIM G` | Generates content from your docs, shows preview |
| `/confirm_broadcast` | Sends the previewed AI content to all users |
| `/cancel` | Cancels pending broadcast |
| `/post_now` | Generates and posts to channel immediately |
| `/post_now Write about Atomy skincare routine` | Posts with custom prompt |
| `/stats` | Shows user count and bot info |

## User Commands

| Command | What it does |
|---|---|
| `/start` | Welcome message |
| `/clear` | Clears conversation history |
| `/myid` | Shows the user their Telegram ID |

## Customizing Auto-Posts

Edit the `CONTENT_PROMPTS` list in `bot.py` to change what kind of content the bot generates for the channel. Each prompt is randomly selected at the scheduled times.

## Costs
- **OpenAI**: ~$1-5/month for low-traffic bot
- **Railway**: Free tier available, then ~$5/month
- **Total**: ~$5-10/month
