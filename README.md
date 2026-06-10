# Telegram Channel Migrator

A robust Python userbot that performs **safe, resumable migration** of all media and files from one Telegram channel to another — without showing "Forwarded From" attribution.

Built with [Telethon](https://github.com/LonamiWebs/Telethon), designed to handle channels with **tens of thousands of files** over multi-day migrations without losing progress or triggering Telegram anti-spam restrictions.

---

## Features

- **Full historical migration** — scans from oldest to newest message
- **Resume support** — saves progress after every transfer; never restarts from scratch
- **Album preservation** — detects and reconstructs media groups (albums)
- **Caption cleanup** — removes DDL references while preserving Telegram formatting
- **No forwarding attribution** — reposts appear as original content
- **Multi-tier rate limiting** — per-minute, per-hour, per-day limits
- **FloodWait handling** — sleeps exactly as long as Telegram requires
- **Exponential backoff** — retries with increasing delays on errors
- **Periodic cooldowns** — automatic pauses after every N files
- **Live monitoring** — watches for new uploads after migration completes
- **Dry-run mode** — scan and log without sending anything
- **SQLite state** — complete migration database with checksums and dedup
- **Structured logging** — console + file logs with progress statistics

---

## Project Structure

```
project/
├── bot.py                 # Entry point — CLI & orchestration
├── config.py              # Configuration loader (.env → dataclass)
├── migrator.py            # Core migration engine
├── caption_processor.py   # DDL cleanup & entity processing
├── album_handler.py       # Album detection & reconstruction
├── rate_limiter.py        # Flood control & rate limiting
├── database.py            # SQLite state persistence
├── progress_tracker.py    # Real-time progress display
├── logger.py              # Structured logging setup
├── .env.example           # Configuration template
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

---

## Prerequisites

- **Python 3.11** or newer
- A Telegram **user account** (not a bot account)
- **API credentials** from [https://my.telegram.org](https://my.telegram.org)
- Admin/post access to the **destination channel**
- Read access to the **source channel**

---

## Installation

### 1. Clone or download this project

```bash
cd telegram-migrator
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/macOS
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
SESSION_NAME=telegram_migrator

SOURCE_CHANNEL=-1001234567890
DESTINATION_CHANNEL=-1009876543210

MIN_DELAY=3
MAX_DELAY=8
MAX_PER_MINUTE=8
MAX_PER_HOUR=200
MAX_PER_DAY=3000

COOLDOWN_EVERY=100
COOLDOWN_MINUTES=10
LARGE_COOLDOWN_EVERY=1000
LARGE_COOLDOWN_MINUTES=45

LIVE_MODE=false
DRY_RUN=false
DATABASE_PATH=migration.db
LOG_LEVEL=INFO
```

#### Finding channel IDs

- **Username**: Use the channel's `@username` (without `@`)
- **Channel ID**: Forward a message from the channel to [@userinfobot](https://t.me/userinfobot) or use the channel URL format `-100XXXXXXXXXX`

---

## Usage

### First run — authenticate

```bash
python bot.py
```

On the first run, Telethon will prompt for your **phone number** and **verification code**. This creates a session file that persists for future runs.

### Standard migration (with resume)

```bash
python bot.py
```

If interrupted (Ctrl+C, crash, power outage), simply run again — it resumes from the last successfully migrated message.

### Dry run — scan without sending

```bash
python bot.py --dry-run
```

Scans the entire source channel, logs what would be migrated, but sends nothing. Useful for estimating migration scope.

### Retry failed messages

```bash
python bot.py --retry
```

Re-attempts all previously failed migrations.

### Live monitoring only

```bash
python bot.py --live-only
```

Skips historical migration and immediately starts watching for new uploads.

### View statistics

```bash
python bot.py --stats
```

Shows migration counts, errors, and progress from the database.

---

## Rate Limiting & Safety

### Recommended settings by risk level

| Setting | Conservative | Moderate | Aggressive |
|---------|-------------|----------|------------|
| `MAX_PER_MINUTE` | 5 | 8 | 15 |
| `MAX_PER_HOUR` | 120 | 200 | 400 |
| `MAX_PER_DAY` | 2000 | 3000 | 5000 |
| `MIN_DELAY` | 5s | 3s | 1s |
| `MAX_DELAY` | 12s | 8s | 3s |
| `COOLDOWN_EVERY` | 50 | 100 | 200 |
| `COOLDOWN_MINUTES` | 15 | 10 | 5 |

> **⚠️ Start with Conservative settings**, especially for new accounts or first runs. If you encounter FloodWait errors, **immediately increase delays**. Telegram monitors activity spikes and may restrict accounts.

### How the safety system works

1. **Random delays** between each send (MIN_DELAY to MAX_DELAY)
2. **Sliding-window rate limits** — per-minute, per-hour, per-day
3. **Regular cooldowns** — e.g., pause 10 min after every 100 files
4. **Large cooldowns** — e.g., pause 45 min after every 1000 files
5. **FloodWait compliance** — sleeps exactly the requested duration + buffer
6. **Exponential backoff** — 5s → 10s → 20s → 40s → ... up to 5 min on errors
7. **Auto-abort** — stops after 20 consecutive errors (safety kill switch)

### Expected migration speed

| Setting | Files/hour | 10K files | 50K files |
|---------|-----------|-----------|-----------|
| Conservative | ~100 | ~4 days | ~20 days |
| Moderate | ~180 | ~2.5 days | ~12 days |
| Aggressive | ~350 | ~1.5 days | ~6 days |

---

## Database

Migration state is stored in SQLite (`migration.db` by default).

### Tables

**migrations** — records every processed message:
| Column | Type | Description |
|--------|------|-------------|
| `source_message_id` | INTEGER (PK) | Original message ID |
| `destination_message_id` | INTEGER | New message ID in destination |
| `media_type` | TEXT | photo, video, document, audio, etc. |
| `album_group_id` | INTEGER | Grouped ID for albums |
| `migrated_at` | TEXT | ISO timestamp |
| `status` | TEXT | success, error, skipped:reason, dry_run |
| `checksum` | TEXT | MD5 for duplicate detection |

**state** — key-value store for misc state.

### Inspecting the database

```bash
sqlite3 migration.db "SELECT status, COUNT(*) FROM migrations GROUP BY status;"
```

---

## Caption Cleanup

The migrator automatically removes DDL references from captions:

- Text containing "DDL" (case-insensitive)
- Hyperlinks with DDL in the URL or display text
- Markdown links: `[DDL Link](https://...)`
- HTML links: `<a href="...">DDL</a>`
- Plain-text DDL URLs
- Multiple DDL occurrences

All other Telegram formatting (bold, italic, code, spoilers, custom emoji, etc.) is preserved.

---

## Album Handling

Media groups (albums) are detected by `grouped_id` and reconstructed in the destination:

- Album order is preserved
- Captions are maintained (typically on the first item)
- If grouped send fails, items are sent individually as fallback
- Album boundaries are detected even across pagination

---

## Logging

Logs are written to both **console** (with emoji indicators) and **file** (in `logs/` directory).

Log events include:
- Startup & configuration
- Channel access validation
- Progress restoration from database
- Each file detection & migration
- Caption modifications
- Album detection & reconstruction
- FloodWait encounters
- Cooldown start/end
- Retry attempts
- Fatal errors

---

## Troubleshooting

### "FloodWaitError: A wait of X seconds is required"
This is normal. The bot automatically sleeps and resumes. If it happens frequently, increase `MIN_DELAY`, `MAX_DELAY`, and reduce `MAX_PER_MINUTE`.

### "Cannot access source/destination channel"
Ensure your user account has joined or been added to both channels. For the destination, you need admin/post permissions.

### "SessionPasswordNeededError"
Your account has 2FA enabled. Enter your password when prompted.

### Migration seems stuck
Check `logs/` for the latest log file. The bot may be in a cooldown period or waiting for a FloodWait to expire.

### Want to start over
Delete `migration.db` to reset all progress. The session file keeps your Telegram login.

---

## License

This project is provided as-is for personal use. Use responsibly and in compliance with Telegram's Terms of Service.
