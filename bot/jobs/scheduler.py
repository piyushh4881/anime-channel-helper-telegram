import os
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client

from database.db import async_session
import database.crud as crud
from services.instagram.service import InstagramService
from bot.client import app as bot_app

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
instagram_service = InstagramService()


async def check_and_publish_scheduled_posts():
    """Polls database for due scheduled posts, publishes them, and notifies users."""
    logger.debug("Checking for due scheduled posts...")
    
    async with async_session() as db:
        due_posts = await crud.get_pending_posts(db)
        
        if not due_posts:
            return
            
        logger.info("Found %d due scheduled posts.", len(due_posts))
        
        for post in due_posts:
            logger.info("Publishing scheduled post %d for user %d...", post.id, post.user_id)
            
            # Helper function for tracking upload status
            async def progress_notify(msg_text: str):
                logger.info("Post %d upload progress: %s", post.id, msg_text)

            # Mark post as publishing
            await crud.update_post(db, post.id, status="publishing")
            
            # Post to Instagram
            success, result = await instagram_service.post_to_instagram(
                db=db,
                post=post,
                progress_callback=progress_notify
            )
            
            # Notify user on Telegram
            try:
                if success:
                    notify_text = (
                        "📅 **Scheduled Post Published Successfully!**\n\n"
                        "Your scheduled post has been posted to Instagram.\n\n"
                        f"🔗 [View on Instagram]({result})"
                    )
                    await bot_app.send_message(
                        chat_id=post.user_id,
                        text=notify_text,
                        disable_web_page_preview=False
                    )
                else:
                    notify_text = (
                        "❌ **Scheduled Post Failed!**\n\n"
                        "Your scheduled post could not be published to Instagram.\n\n"
                        f"Error message:\n`{result}`"
                    )
                    await bot_app.send_message(chat_id=post.user_id, text=notify_text)
            except Exception as e:
                logger.error("Failed to notify user %d about scheduled post result: %s", post.user_id, e)
                
            # Cleanup media files
            for file_path in post.media_files:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info("Cleaned up temporary scheduled file: %s", file_path)
                except Exception as ex:
                    logger.warning("Failed to clean up scheduled file %s: %s", file_path, ex)


def start_scheduler():
    """Starts the background AsyncIOScheduler."""
    if not scheduler.running:
        scheduler.add_job(
            check_and_publish_scheduled_posts,
            "interval",
            minutes=1,
            next_run_time=datetime.now(),
            id="publish_scheduler"
        )
        scheduler.start()
        logger.info("APScheduler started successfully.")


def shutdown_scheduler():
    """Stops the background AsyncIOScheduler."""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("APScheduler stopped successfully.")
