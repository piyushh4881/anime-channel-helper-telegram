# 🎬 Movie Indexer & AniList Metadata Telegram Bot

A production-ready Telegram bot built in Python using Telethon. It automatically scans channel history for movie/anime releases (specifically `.mkv` files), fetches metadata from **AniList**, cleans up filenames/captions, and maintains a beautifully grouped index in your Private Messages.

---

## 🌟 Key Features

### 1. Automated Caption Renaming & Cleaning
- Monitors the channel for new `.mkv` uploads.
- Cleans up filename tags: strips bracketed tags (e.g. `[bonkai77]`, `[izu]`) and leading release group prefixes (e.g. `bonkai77_`, `RigAV1_`, `Rig_`).
- Parses release years (e.g. `2024`, `2025`) even if they are not in parentheses.
- Fetches the English and Romaji titles from **AniList**, merging them into the standard format:  
  `**Romaji Title // English Title (Year) - Studio**` (e.g., `**Umi ga Kikoeru // Ocean Waves (1993) - Studio Ghibli**`).
- If no year is present in the filename, it falls back to querying the release year from AniList.

### 2. Preceding Message Metadata Parsing
- When indexing, the bot searches up to 10 messages backward in the channel to locate preceding photo/info cards.
- Extracts the main title and parsed studio names.
- Filters out non-animation companies (e.g. *Aniplex*, *Bandai Visual*, *Shueisha*) using a built-in blacklist to extract the correct animation studio name.

### 3. Personal Index in Admin PM
- Generates a paginated, flat index sent directly to the admin's PM (to keep the channel clean).
- Grouped and sorted alphabetically by **Studio Names** (e.g., `A-1 Pictures`, `Studio Ghibli`).
- Movies under each studio group are sorted alphabetically.
- Removes bold formatting inside the movie link brackets for a cleaner look.
- Cleans up and deletes older index messages in the PM before posting a refreshed one.
- Avoids duplicate movie entries by only listing the primary release link.

### 4. Interactive AniList Search Command
- Trigger: Send `.ani <query>` or `/ani <query>` in the channel.
- The bot deletes your trigger message, fetches metadata from AniList (English/Romaji/Native titles, type, status, episodes, duration, score, genres, studios, description), downloads the high-res poster image, and posts a premium, stylized info card with the poster.

### 5. Multi-Day Scanning & Resume Support
- Handles large channels (50k+ messages) using chronological ID batches of 100 to bypass bot `iter_messages` constraints.
- Retains checkpoints (`scan_state`) in SQLite, enabling safe resume if interrupted.

---

## 📂 Project Structure

```text
movie_indexer/
├── logs/                   # Bot run logs
├── anilist.py              # AniList GraphQL API client
├── bot.py                  # Entry point (initialization, dual client setup)
├── commands.py             # Command handler registrations & listeners
├── config.py               # Configuration loader & validation
├── database.py             # SQLite CRUD layer
├── filename_cleaner.py     # Filename parsing, year extraction & group stripping
├── index_builder.py        # PM index formatting, sorting & pagination
├── models.py               # Typed dataclasses (Movie, Release, ScanState)
├── requirements.txt        # Package dependencies
├── .env.example            # Environment variables template
└── README.md               # This documentation file
```

---

## ⚙️ Configuration & Secrets (.env)

Duplicate `.env.example` to `.env` in the `movie_indexer` directory and configure the variables:

```env
# ── Telegram API credentials (https://my.telegram.org) ──
API_ID=your_api_id
API_HASH=your_api_hash

# ── Bot token from @BotFather ──
BOT_TOKEN=your_bot_token

# ── Target channel to monitor & scan ──
# Include the -100 prefix (e.g. -1003906783306)
CHANNEL_ID=-100xxxxxxxxxx

# ── Admin user IDs allowed to run commands in PM ──
# Comma-separated list of Telegram user IDs (e.g. 123456789)
ADMIN_USERS=123456789

# ── Database & Rate Limits ──
DATABASE_PATH=movie_indexer.db
SCAN_BATCH_SIZE=100
RATE_LIMIT_DELAY=0.5
LOG_LEVEL=INFO
```

---

## 🚀 Installation & Running

### 1. Setup Environment
Ensure Python 3.8+ is installed on your system.

```bash
# Navigate to project directory
cd movie_indexer

# Create and activate virtual environment
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Startup
```bash
python bot.py
```

---

## 🤖 Bot Commands

Commands are accepted in the bot's private messages (PM) from whitelisted admins, or directly in the target channel.

- `/scan` — Clears the database and runs a full, chronological channel scan (oldest to newest message), re-renames captions, and posts a fresh sorted index to your PM.
- `/update` — Performs an incremental scan starting from the last scanned message ID.
- `/rebuild` — Re-generates and posts the sorted, grouped index to your PM using database entries (without re-scanning).
- `/search <query>` — Performs a database search for movies matching the query.
- `/stats` — Displays database statistics (total movies, releases, and studios).
- `.ani <query>` or `/ani <query>` — (Channel/PM) Searches AniList and posts a stylized metadata card with the movie poster (deletes the trigger message).

---

## 💾 Database Schema

The bot uses SQLite (`movie_indexer.db`) with WAL mode enabled for high concurrent read performance.

### 1. `movies` Table
Stores unique titles fetched from files/metadata.
- `id` (INTEGER, PK, Auto-increment)
- `title` (TEXT, Not Null, Unique with year)
- `year` (INTEGER)
- `clean_name` (TEXT, Not Null) — formatted `Title // English (Year)`
- `studio` (TEXT)

### 2. `releases` Table
Tracks file posts (resolutions, messages, deep-links) mapped to movies.
- `id` (INTEGER, PK, Auto-increment)
- `movie_id` (INTEGER, FK)
- `quality` (TEXT) — extracted resolution and format (e.g. `1080p BD`)
- `message_id` (INTEGER)
- `channel_id` (INTEGER)
- `telegram_link` (TEXT)

### 3. `scan_state` Table
Persists progress coordinates for resuming scans.
- `key` (TEXT, PK)
- `value` (TEXT)

---

## 🔒 Safety & Rate Limiting
- Built-in `FloodWait` compliance: if Telegram throws a rate limit warning, the bot pauses and sleeps for the exact duration requested.
- AniList queries are throttled with a `0.7s` delay between consecutive requests to stay safely under the `90 requests/minute` GraphQL limit.
- Unicode console outputs are reconfigured to `utf-8` to support Japanese character representations natively on Windows systems.
