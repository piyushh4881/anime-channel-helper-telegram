import sys
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from database.db import async_session
import database.crud as crud
from config import Config

logger = logging.getLogger(__name__)


async def is_admin_check(user_id: int) -> bool:
    """Verifies user role is admin or in Config.ADMINS."""
    if user_id in Config.ADMINS:
        return True
    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        return user is not None and user.role == "admin"


@Client.on_callback_query(filters.regex("^menu_admin$"))
async def admin_menu_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        await callback_query.answer("⚠️ Access restricted to administrators.", show_alert=True)
        return

    async with async_session() as db:
        admin_only = await crud.get_system_setting(db, "admin_only", Config.ADMIN_ONLY)
        maintenance = await crud.get_system_setting(db, "maintenance_mode", Config.MAINTENANCE_MODE)

    await callback_query.message.edit_text(
        "🛠️ **Admin Dashboard**\n\n"
        "Configure operational toggles, monitor statistics, inspect logs, or restart the server process.",
        reply_markup=builders.get_admin_keyboard(admin_only, maintenance)
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^admin_toggle_ao$"))
async def toggle_admin_only_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    async with async_session() as db:
        current = await crud.get_system_setting(db, "admin_only", Config.ADMIN_ONLY)
        new_val = not current
        await crud.set_system_setting(db, "admin_only", new_val)
        await crud.add_log(db, user_id, "admin_toggle_admin_only", f"Set to {new_val}")
        
        maintenance = await crud.get_system_setting(db, "maintenance_mode", Config.MAINTENANCE_MODE)

    await callback_query.message.edit_text(
        "🛠️ **Admin Dashboard**\n\n"
        "Configure operational toggles, monitor statistics, inspect logs, or restart the server process.",
        reply_markup=builders.get_admin_keyboard(new_val, maintenance)
    )
    await callback_query.answer(f"Admin-Only Mode turned {'ON' if new_val else 'OFF'}")


@Client.on_callback_query(filters.regex("^admin_toggle_m$"))
async def toggle_maintenance_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    async with async_session() as db:
        current = await crud.get_system_setting(db, "maintenance_mode", Config.MAINTENANCE_MODE)
        new_val = not current
        await crud.set_system_setting(db, "maintenance_mode", new_val)
        await crud.add_log(db, user_id, "admin_toggle_maintenance", f"Set to {new_val}")
        
        admin_only = await crud.get_system_setting(db, "admin_only", Config.ADMIN_ONLY)

    await callback_query.message.edit_text(
        "🛠️ **Admin Dashboard**\n\n"
        "Configure operational toggles, monitor statistics, inspect logs, or restart the server process.",
        reply_markup=builders.get_admin_keyboard(admin_only, new_val)
    )
    await callback_query.answer(f"Maintenance Mode turned {'ON' if new_val else 'OFF'}")


@Client.on_callback_query(filters.regex("^admin_view_stats$"))
async def view_stats_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    async with async_session() as db:
        stats = await crud.get_statistics(db)

    stats_text = (
        "📊 **Bot Statistics Dashboard**\n\n"
        f"👥 **Total Registered Users:** {stats['total_users']}\n"
        f"📬 **Total Posts Configured:** {stats['total_posts']}\n"
        f"✅ **Successful Posts:** {stats['success_posts']}\n"
        f"❌ **Failed Posts:** {stats['failed_posts']}\n"
        f"📅 **Pending (Scheduled) Posts:** {stats['pending_posts']}\n\n"
        "🔗 **Instagram Status:**\n"
        f"• **Status:** {stats['instagram_status']}\n"
        f"• **Connected Account:** @{stats['instagram_username']}\n"
        f"• **Auth Method:** {stats['instagram_mode'].upper()}\n"
    )

    await callback_query.message.edit_text(stats_text, reply_markup=builders.get_back_button("menu_admin"))
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^admin_view_logs$"))
async def view_logs_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    async with async_session() as db:
        logs = await crud.get_logs(db, limit=15)

    log_lines = []
    for log in logs:
        time_str = log.created_at.strftime("%H:%M:%S")
        user_str = f"U:{log.user_id} " if log.user_id else ""
        details_str = f" - {log.details}" if log.details else ""
        log_lines.append(f"• `[{time_str}]` **{log.action}**\n  ↳ {user_str}{details_str}")

    logs_text = (
        "📁 **Recent Action Audit Logs** (Last 15):\n\n"
        + ("\n".join(log_lines) if log_lines else "No logs recorded yet.")
    )

    await callback_query.message.edit_text(logs_text, reply_markup=builders.get_back_button("menu_admin"))
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^admin_restart_bot$"))
async def restart_bot_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    await callback_query.message.edit_text("🔄 **Bot process is restarting...**\n\nThe service will be online shortly.")
    await callback_query.answer("Restarting process...")
    
    # Log restart
    async with async_session() as db:
        await crud.add_log(db, user_id, "admin_restart_triggered")

    logger.info("Restart triggered by admin %d. Exiting process...", user_id)
    # Exits the process, container or process runner will automatically restart it.
    sys.exit(0)


# --- Commands for Admin User Management ---

@Client.on_callback_query(filters.regex("^admin_manage_admins$"))
async def manage_admins_guide_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await is_admin_check(user_id):
        return

    async with async_session() as db:
        # Fetch admins
        from sqlalchemy import select
        result = await db.execute(select(crud.User).where(crud.User.role == "admin"))
        admins = list(result.scalars().all())

    admin_list = []
    for admin in admins:
        user_str = f"@{admin.username}" if admin.username else "No Username"
        admin_list.append(f"• `{admin.id}` ({user_str})")

    manage_text = (
        "⚙️ **Admin User Management**\n\n"
        "**Current Admins:**\n"
        + "\n".join(admin_list)
        + "\n\n"
        "**To add or remove admins, send one of these commands in the chat:**\n"
        "• `/add_admin [Telegram User ID]`\n"
        "• `/remove_admin [Telegram User ID]`\n\n"
        "Example: `/add_admin 123456789`"
    )
    await callback_query.message.edit_text(manage_text, reply_markup=builders.get_back_button("menu_admin"))
    await callback_query.answer()


@Client.on_message(filters.command("add_admin") & filters.private)
async def add_admin_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Only super-admins in env list can add/remove admins
    if user_id not in Config.ADMINS:
        await message.reply_text("❌ Only super-administrators defined in the system configurations can manage roles.")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("⚠️ Usage: `/add_admin [Telegram User ID]`")
        return

    target_id = int(parts[1])
    async with async_session() as db:
        target_user = await crud.get_user(db, target_id)
        if not target_user:
            # Create user entry
            target_user = await crud.get_or_create_user(db, target_id)
            
        target_user.role = "admin"
        await db.commit()
        await crud.add_log(db, user_id, "add_admin", f"Added user {target_id}")

    await message.reply_text(f"✅ User `{target_id}` has been successfully set to **Admin** role.")


@Client.on_message(filters.command("remove_admin") & filters.private)
async def remove_admin_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in Config.ADMINS:
        await message.reply_text("❌ Only super-administrators defined in the system configurations can manage roles.")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("⚠️ Usage: `/remove_admin [Telegram User ID]`")
        return

    target_id = int(parts[1])
    if target_id in Config.ADMINS:
        await message.reply_text("❌ You cannot revoke roles of super-administrators configured in environment variables.")
        return

    async with async_session() as db:
        target_user = await crud.get_user(db, target_id)
        if target_user:
            target_user.role = "user"
            await db.commit()
            await crud.add_log(db, user_id, "remove_admin", f"Removed user {target_id}")
            success = True
        else:
            success = False

    if success:
        await message.reply_text(f"✅ User `{target_id}` has been reverted to standard user role.")
    else:
        await message.reply_text(f"❌ User `{target_id}` does not exist in the database.")


# --- Ban / Unban Command Handlers ---

@Client.on_message(filters.command("ban") & filters.private)
async def ban_user_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin_check(user_id):
        await message.reply_text("❌ Access denied.")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("⚠️ Usage: `/ban [Telegram User ID]`")
        return

    target_id = int(parts[1])
    if target_id in Config.ADMINS or target_id == user_id:
        await message.reply_text("❌ You cannot ban yourself or super-administrators.")
        return

    async with async_session() as db:
        success = await crud.ban_user(db, target_id, ban=True)

    if success:
        await message.reply_text(f"✅ User `{target_id}` has been successfully banned.")
    else:
        await message.reply_text(f"❌ User `{target_id}` not found in database.")


@Client.on_message(filters.command("unban") & filters.private)
async def unban_user_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if not await is_admin_check(user_id):
        await message.reply_text("❌ Access denied.")
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text("⚠️ Usage: `/unban [Telegram User ID]`")
        return

    target_id = int(parts[1])
    async with async_session() as db:
        success = await crud.ban_user(db, target_id, ban=False)

    if success:
        await message.reply_text(f"✅ User `{target_id}` ban has been successfully lifted.")
    else:
        await message.reply_text(f"❌ User `{target_id}` not found in database.")
