"""
Access-control decorators.
"""

import functools
import logging
from typing import Callable, Any

from telegram import Update
from telegram.ext import ContextTypes

from config import Config

logger = logging.getLogger(__name__)


def owner_only(func: Callable) -> Callable:
    """Decorator that restricts a handler to the bot owner only."""

    @functools.wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> Any:
        user = update.effective_user
        if user is None or user.id != Config.OWNER_ID:
            logger.warning(
                "Unauthorized access attempt by user %s (%s)",
                user.id if user else "unknown",
                user.username if user else "unknown",
            )
            # Silently ignore unauthorized users
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper
