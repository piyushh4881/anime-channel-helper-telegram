import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import Config
from database.db import engine, Base
from bot.client import app as bot_app
from bot.jobs.scheduler import start_scheduler, shutdown_scheduler
from api.routes import router

logger = logging.getLogger(__name__)

# Absolute path to download directory
DOWNLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "downloads"))
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the startup and shutdown lifecycles of DB tables, background jobs, and Telegram Bot."""
    logger.info("Initializing system startup lifecycles...")
    
    # 1. Initialize Database Tables
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schemas verified and created successfully.")
    except Exception as e:
        logger.critical("Database initialization failed: %s", e)
        raise e

    # 2. Start Background Scheduler (APScheduler)
    try:
        start_scheduler()
    except Exception as e:
        logger.error("Failed to start APScheduler: %s", e)

    # 3. Start Pyrogram Bot
    try:
        await bot_app.start()
        logger.info("Pyrogram Telegram Bot started successfully.")
    except Exception as e:
        logger.critical("Failed to start Pyrogram Bot: %s", e)
        raise e

    yield

    logger.info("Initializing system shutdown lifecycles...")
    
    # 1. Stop Pyrogram Bot
    try:
        await bot_app.stop()
        logger.info("Pyrogram Bot stopped gracefully.")
    except Exception as e:
        logger.error("Error stopping Pyrogram Bot: %s", e)

    # 2. Shutdown Scheduler
    try:
        shutdown_scheduler()
    except Exception as e:
        logger.error("Error shutting down APScheduler: %s", e)
        
    # 3. Dispose engine
    await engine.dispose()
    logger.info("Database connections closed.")


app = FastAPI(
    title="Telegram-to-Instagram Scheduler API",
    description="Web backend and OAuth handler for Telegram-to-Instagram Bot",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static media directory for serving files to Facebook Graph API
app.mount("/media", StaticFiles(directory=DOWNLOADS_DIR), name="media")

# Include routers
app.include_router(router)
