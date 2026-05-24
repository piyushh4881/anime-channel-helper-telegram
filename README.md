# 🤖 Telegram Scheduler Bot

A **production-ready** Telegram automation bot for scheduling and posting messages to channels. Built with `python-telegram-bot` v21+, APScheduler, and SQLite.

---

## ✨ Features

| Feature | Description |
|---|---|
| **Owner-Only Access** | All commands restricted to a single admin via `OWNER_ID` |
| **Message Scheduler** | Auto-post queued messages at configurable intervals |
| **Multi-Channel Support** | Post to multiple Telegram channels simultaneously |
| **Auto-Post Mode** | Send anything to the bot PM → automatically queued |
| **Instant Forward** | Skip the queue — forward messages immediately |
| **Media Support** | Photos, videos, documents, captions, media groups |
| **Queue Management** | Preview, delete, clear, and inspect queued items |
| **Admin Panel** | Inline keyboard panel with live controls |
| **Broadcast** | Send announcements to all registered users |
| **Randomized Intervals** | Add jitter to posting times to appear natural |
| **Persistent State** | Scheduler state survives restarts |
| **Railway Ready** | Deploy to Railway with zero configuration changes |

---

## 📁 Project Structure

```
telegram_scheduler_bot/
├── bot.py                          # Main entry point
├── config.py                       # Environment config loader
├── handlers/
│   ├── __init__.py
│   ├── start.py                    # /start, /help
│   ├── scheduler_handlers.py       # /setinterval, /startscheduler, /stopscheduler
│   ├── queue_handlers.py           # /queue, /deletequeue, /clearqueue, /instant
│   ├── channel_handlers.py         # /setchannel, /autopost, auto-queue logic
│   ├── stats_handlers.py           # /stats
│   ├── broadcast.py                # /broadcast
│   └── admin.py                    # /admin inline panel
├── database/
│   ├── __init__.py
│   └── db.py                       # SQLite database layer
├── scheduler/
│   ├── __init__.py
│   └── scheduler.py                # APScheduler wrapper
├── utils/
│   ├── __init__.py
│   ├── decorators.py               # @owner_only access control
│   ├── formatting.py               # HTML/Markdown formatting
│   └── helpers.py                  # Interval parsing, uptime, cleanup
├── requirements.txt
├── Procfile                        # Railway/Heroku process file
├── runtime.txt                     # Python version specifier
├── .env.example                    # Template for environment variables
├── .gitignore
└── README.md
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram User ID (from [@userinfobot](https://t.me/userinfobot))
- A Telegram channel where the bot is an admin

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/telegram_scheduler_bot.git
cd telegram_scheduler_bot
```

### 2. Create Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```env
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
OWNER_ID=123456789
CHANNELS=@your_channel,-1001234567890
```

### 5. Run the Bot

```bash
python bot.py
```

---

## 📋 Command Reference

### General
| Command | Description |
|---|---|
| `/start` | Register and show welcome message |
| `/help` | Show all available commands |

### Scheduler
| Command | Description |
|---|---|
| `/setinterval <time>` | Set posting interval (e.g., `30s`, `5m`, `2h`, `1d`, `1h30m`) |
| `/startscheduler` | Start automatic posting |
| `/stopscheduler` | Stop automatic posting |

### Queue
| Command | Description |
|---|---|
| `/queue` | Show all pending messages |
| `/previewqueue` | Preview the next message to be sent |
| `/deletequeue <id>` | Remove a specific item by ID |
| `/clearqueue` | Clear all pending items |
| `/instant` | Immediately send the next queued item |

### Channels
| Command | Description |
|---|---|
| `/setchannel <id>` | Add a target channel |
| `/listchannels` | List all target channels |
| `/removechannel <id>` | Remove a target channel |

### Modes
| Command | Description |
|---|---|
| `/autopost` | Toggle auto-queue mode (PM → queue) |

### Admin
| Command | Description |
|---|---|
| `/stats` | Show bot statistics |
| `/admin` | Open admin control panel |
| `/broadcast <msg>` | Broadcast message to all users |

---

## ⚙️ Admin Panel

The `/admin` command opens an interactive inline keyboard panel with:

- ▶️/⏹ **Start/Stop Scheduler**
- ✅/❌ **Toggle Auto-Post**
- ⚡/❌ **Toggle Instant Forward**
- 🎲/❌ **Toggle Randomized Intervals**
- 🗑 **Clear Queue**
- 📋 **View Recent Logs**
- 🔄 **Refresh Panel**

---

## 🔄 Auto-Post Mode

When **Auto-Post** is enabled:

1. Send any message (text, photo, video, document) to the bot's PM
2. It gets automatically added to the queue
3. The scheduler posts items one-by-one to your channels

### Instant Forward Mode

When **Instant Forward** is also enabled:
- Messages skip the queue entirely
- They are forwarded to channels immediately upon receipt

---

## 📊 Database Schema

| Table | Purpose |
|---|---|
| `users` | Stores all users who used `/start` |
| `queue` | Message queue with media type, content, file IDs, status |
| `stats` | Aggregated send/fail counts |
| `settings` | Key-value persistent settings |
| `logs` | Activity logs with auto-cleanup |
| `channels` | Dynamically added target channels |

---

## 🚂 Railway Deployment

### Step 1: Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/your-username/telegram_scheduler_bot.git
git push -u origin main
```

### Step 2: Create Railway Project

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your `telegram_scheduler_bot` repository

### Step 3: Set Environment Variables

In the Railway dashboard, go to **Variables** and add:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Your bot token from BotFather |
| `OWNER_ID` | Your Telegram user ID |
| `CHANNELS` | `@channel1,-100123456` |
| `DATABASE_PATH` | `bot_database.db` |
| `DEFAULT_INTERVAL` | `3600` |
| `AUTO_POST_MODE` | `false` |
| `PARSE_MODE` | `HTML` |
| `MAX_RETRIES` | `3` |
| `LOG_LEVEL` | `INFO` |
| `LOG_RETENTION_DAYS` | `7` |

### Step 4: Configure Build

Railway will auto-detect the `Procfile`:
```
worker: python bot.py
```

> ⚠️ Make sure the service type is set to **Worker** (not Web) since this bot uses polling, not webhooks.

### Step 5: Deploy

Click **Deploy** — Railway will install dependencies and start the bot.

### Persistent Storage (Optional)

For SQLite persistence across deploys, attach a **Railway Volume**:

1. In your service settings, click **"Add Volume"**
2. Mount path: `/data`
3. Update `DATABASE_PATH` env var to `/data/bot_database.db`

---

## 🐳 Docker Deployment (Alternative)

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
```

```bash
docker build -t telegram-scheduler-bot .
docker run -d --env-file .env telegram-scheduler-bot
```

---

## 🔒 Security

- ✅ All secrets loaded from environment variables
- ✅ No hardcoded tokens or IDs
- ✅ `@owner_only` decorator on every command
- ✅ Unauthorized users are silently ignored
- ✅ `.env` file excluded via `.gitignore`

---

## 🛠️ Development

### Adding a New Command

1. Create a handler function in `handlers/`:
   ```python
   from utils.decorators import owner_only

   @owner_only
   async def my_command(update, context):
       await update.message.reply_text("Hello!")
   ```

2. Register it in `bot.py`:
   ```python
   app.add_handler(CommandHandler("mycommand", my_command))
   ```

### Interval Format Reference

| Input | Seconds |
|---|---|
| `30` or `30s` | 30 |
| `5m` | 300 |
| `2h` | 7,200 |
| `1d` | 86,400 |
| `1h30m` | 5,400 |

---

## 📝 License

MIT License — use freely for personal and commercial projects.

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
