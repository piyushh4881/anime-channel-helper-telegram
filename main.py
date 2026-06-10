import uvicorn
import logging
import sys
from config import Config

# Configure global logging
logging.basicConfig(
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Mute chatty libraries
for noisy in ("pyrogram.session", "pyrogram.connection", "pyrogram.dispatcher", "httpx", "apscheduler.scheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


if __name__ == "__main__":
    Config.validate()
    
    # Run Uvicorn Async Web Server
    logging.info("Starting Telegram-to-Instagram Bot Service on port %d...", Config.PORT)
    uvicorn.run("api.main:app", host="0.0.0.0", port=Config.PORT)
