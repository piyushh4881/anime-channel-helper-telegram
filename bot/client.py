from pyrogram import Client
from config import Config

app = Client(
    name="manga_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=Config.WORKERS,
    plugins=dict(root="bot.handlers"),
)
