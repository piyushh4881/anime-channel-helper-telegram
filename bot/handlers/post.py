import os
import asyncio
import logging
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from bot.state import UserState
from database.db import async_session
import database.crud as crud
from utils.image_processor import fit_aspect_ratio, apply_watermark, resize_and_compress, validate_image
from services.instagram.service import InstagramService

logger = logging.getLogger(__name__)

# Temporary buffer for media group (carousel) messages
# media_group_id -> {"user_id": int, "messages": [Message], "lock": asyncio.Lock}
MEDIA_GROUPS = {}
DOWNLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "downloads"))
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

instagram_service = InstagramService()


def get_media_group_id(message: Message) -> str | None:
    return message.media_group_id


async def show_editor_preview(client: Client, chat_id: int, user_id: int, state_data: dict, edit_message_id: int = None):
    """Generates and sends the interactive post editor preview screen."""
    caption = state_data.get("caption", "")
    media_files = state_data.get("media_files", [])
    media_type = state_data.get("media_type", "image")
    
    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        settings = user.settings or {}
        watermark_enabled = settings.get("watermark_enabled", False)
        watermark_text = settings.get("watermark_text", "")
        crop_mode = settings.get("crop_mode", "none")

    preview_text = (
        "📝 **Instagram Post Editor**\n\n"
        f"📖 **Caption:**\n{caption or '_None (You will be prompted for one)_'}\n\n"
        f"📦 **Media Type:** {media_type.capitalize()} ({len(media_files)} items)\n"
        f"📐 **Aspect Ratio:** {crop_mode.upper()}\n"
        f"💧 **Watermark:** {'ON ✅' if watermark_enabled else 'OFF ❌'} (Text: `{watermark_text}`)\n"
        f"📅 **Scheduled Time:** {state_data.get('scheduled_at', 'Now ⚡')}\n\n"
        "Configure your post using the buttons below:"
    )

    keyboard = builders.get_post_editor_keyboard(watermark_enabled, watermark_text, crop_mode)
    
    if edit_message_id:
        try:
            await client.edit_message_text(chat_id, edit_message_id, preview_text, reply_markup=keyboard)
            return
        except Exception:
            pass
            
    await client.send_message(chat_id, preview_text, reply_markup=keyboard)


# --- Media Receivers ---

@Client.on_message(filters.photo & filters.private)
async def photo_handler(client: Client, message: Message):
    """Handles incoming single photos and photo albums."""
    user_id = message.from_user.id
    if not await check_user_access(client, message):
        return

    # Check if this is part of a media group (album)
    mg_id = get_media_group_id(message)
    if mg_id:
        if mg_id not in MEDIA_GROUPS:
            MEDIA_GROUPS[mg_id] = {
                "user_id": user_id,
                "messages": [message],
                "lock": asyncio.Lock()
            }
            # Start background task to collect all album pieces
            asyncio.create_task(collect_media_group(client, mg_id))
        else:
            async with MEDIA_GROUPS[mg_id]["lock"]:
                MEDIA_GROUPS[mg_id]["messages"].append(message)
        return

    # Handle single photo upload
    status_msg = await message.reply_text("📥 Downloading photo...")
    
    try:
        # Download
        file_path = await message.download(file_name=os.path.join(DOWNLOADS_DIR, f"{user_id}_{message.id}.jpg"))
        
        # Save into user state
        state_data = {
            "media_files": [file_path],
            "media_type": "image",
            "caption": message.caption or "",
            "scheduled_at": None
        }
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        
        # Next flow step: if no caption, ask user. Otherwise show preview
        if not message.caption:
            await UserState.set_state(user_id, "POST_WAIT_CAPTION", state_data)
            await status_msg.edit_text(
                "✍️ **Please send the caption for your post.**\n"
                "Or click the button below to skip.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Skip Caption ➡️", callback_data="skip_caption")]])
            )
        else:
            await status_msg.delete()
            await show_editor_preview(client, message.chat.id, user_id, state_data)
            
    except Exception as e:
        logger.error("Failed to process photo: %s", e)
        await status_msg.edit_text(f"❌ Failed to process image: {e}")


async def collect_media_group(client: Client, mg_id: str):
    """Gathers all photos in a media group within a 1.2-second window."""
    await asyncio.sleep(1.2)
    group_data = MEDIA_GROUPS.pop(mg_id, None)
    if not group_data:
        return

    messages = group_data["messages"]
    user_id = group_data["user_id"]
    chat_id = messages[0].chat.id

    status_msg = await client.send_message(chat_id, f"📥 Downloading album ({len(messages)} images)...")
    
    try:
        # Sort messages by message ID to preserve user order
        messages.sort(key=lambda m: m.id)
        
        download_tasks = []
        for idx, msg in enumerate(messages):
            if msg.photo:
                path = os.path.join(DOWNLOADS_DIR, f"{user_id}_{msg.id}_{idx}.jpg")
                download_tasks.append(msg.download(file_name=path))
        
        file_paths = await asyncio.gather(*download_tasks)
        file_paths = [p for p in file_paths if p] # filter failed downloads
        
        # Extract caption from the first message that has it
        caption = ""
        for msg in messages:
            if msg.caption:
                caption = msg.caption
                break

        state_data = {
            "media_files": file_paths,
            "media_type": "carousel" if len(file_paths) > 1 else "image",
            "caption": caption,
            "scheduled_at": None
        }
        
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        
        if not caption:
            await UserState.set_state(user_id, "POST_WAIT_CAPTION", state_data)
            await status_msg.edit_text(
                "✍️ **Please send the caption for your Carousel post.**\n"
                "Or click the button below to skip.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Skip Caption ➡️", callback_data="skip_caption")]])
            )
        else:
            await status_msg.delete()
            await show_editor_preview(client, chat_id, user_id, state_data)

    except Exception as e:
        logger.error("Failed to collect media group: %s", e)
        await status_msg.edit_text(f"❌ Failed to process album: {e}")


# Custom filters for post FSM states
async def post_state_filter_func(_, __, message: Message) -> bool:
    user_id = message.from_user.id
    state, _ = await UserState.get_state(user_id)
    return state in ("POST_WAIT_CAPTION", "POST_WAIT_HASHTAGS", "POST_WAIT_SCHEDULE")

post_state = filters.create(post_state_filter_func)


# --- Conversational State Handlers ---

@Client.on_message(post_state & filters.text & filters.private)
async def text_state_router(client: Client, message: Message):
    """Route text input based on the user's FSM state."""
    user_id = message.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if not state:
        # Default text inputs are ignored or treated as help
        return

    if state == "POST_WAIT_CAPTION":
        state_data["caption"] = message.text
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        await show_editor_preview(client, message.chat.id, user_id, state_data)
        await message.delete() # clean chat
        
    elif state == "POST_WAIT_HASHTAGS":
        hashtags = message.text.strip()
        existing_caption = state_data.get("caption", "")
        # Append hashtags
        state_data["caption"] = f"{existing_caption}\n\n{hashtags}".strip()
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        await show_editor_preview(client, message.chat.id, user_id, state_data)
        await message.delete()

    elif state == "POST_WAIT_SCHEDULE":
        # Expect date format: "YYYY-MM-DD HH:MM" or relative "+15m", "+2h"
        text = message.text.strip()
        scheduled_dt = None
        
        try:
            if text.startswith("+"):
                # Relative time
                amount = int(text[1:-1])
                unit = text[-1].lower()
                if unit == "m":
                    scheduled_dt = datetime.now() + timedelta(minutes=amount)
                elif unit == "h":
                    scheduled_dt = datetime.now() + timedelta(hours=amount)
                elif unit == "d":
                    scheduled_dt = datetime.now() + timedelta(days=amount)
                else:
                    raise ValueError("Invalid unit. Use m, h, or d.")
            else:
                # Absolute time
                scheduled_dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
                
            if scheduled_dt <= datetime.now():
                await message.reply_text("⚠️ Time must be in the future! Please try again:")
                return

            state_data["scheduled_at"] = scheduled_dt.strftime("%Y-%m-%d %H:%M")
            await UserState.set_state(user_id, "POST_EDITOR", state_data)
            await show_editor_preview(client, message.chat.id, user_id, state_data)
            await message.delete()
            
        except Exception as e:
            await message.reply_text(
                "❌ **Invalid format!**\n\n"
                "Please use:\n"
                "• `YYYY-MM-DD HH:MM` (e.g. `2026-06-01 15:30`)\n"
                "• `+Xm` / `+Xh` / `+Xd` (e.g. `+30m` for 30 mins, `+2h` for 2 hours)\n\n"
                "Send new time:"
            )


# --- Callback Handlers for Post Creation ---

@Client.on_callback_query(filters.regex("^skip_caption$"))
async def skip_caption_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state == "POST_WAIT_CAPTION":
        state_data["caption"] = ""
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_edit_caption$"))
async def edit_caption_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        await UserState.set_state(user_id, "POST_WAIT_CAPTION", state_data)
        await callback_query.message.edit_text(
            "✍️ **Send the new caption for this post:**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="post_preview_back")]])
        )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_add_hashtags$"))
async def add_hashtags_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        # Load user hashtag presets if any to display as inline buttons
        async with async_session() as db:
            presets = await crud.get_hashtag_presets(db, user_id)
            
        buttons = []
        for preset in presets:
            buttons.append([InlineKeyboardButton(f"🏷️ {preset.name}", callback_data=f"post_apply_preset_{preset.id}")])
            
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="post_preview_back")])
        
        await UserState.set_state(user_id, "POST_WAIT_HASHTAGS", state_data)
        await callback_query.message.edit_text(
            "🏷️ **Send the hashtags to append to the caption (e.g. `#nature #summer`), or select from your presets below:**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_apply_preset_(\\d+)$"))
async def apply_preset_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    preset_id = int(callback_query.matches[0].group(1))
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        async with async_session() as db:
            preset = await db.get(crud.HashtagPreset, preset_id)
            if preset and preset.user_id == user_id:
                existing_caption = state_data.get("caption", "")
                state_data["caption"] = f"{existing_caption}\n\n{preset.hashtags}".strip()
                await UserState.set_state(user_id, "POST_EDITOR", state_data)
                
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer("Hashtag preset applied!")


@Client.on_callback_query(filters.regex("^post_crop_mode$"))
async def crop_mode_menu_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        await callback_query.message.edit_text(
            "📐 **Select the aspect ratio for your post:**\n\n"
            "• **Square (1:1)**: Standard square format.\n"
            "• **Portrait (4:5)**: Vertical format (takes up more screen space on Instagram).\n"
            "• **Original**: Upload file without modifying margins.",
            reply_markup=builders.get_crop_keyboard()
        )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^crop_(none|1:1|4:5|back)$"))
async def select_crop_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    choice = callback_query.matches[0].group(1)
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        if choice != "back":
            async with async_session() as db:
                await crud.update_user_settings(db, user_id, {"crop_mode": choice})
        
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_toggle_watermark$"))
async def toggle_watermark_post_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        async with async_session() as db:
            user = await crud.get_user(db, user_id)
            settings = user.settings or {}
            current_val = settings.get("watermark_enabled", False)
            # Toggle value
            await crud.update_user_settings(db, user_id, {"watermark_enabled": not current_val})
            
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer("Watermark toggled!")


@Client.on_callback_query(filters.regex("^post_preview_back$"))
async def preview_back_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        await UserState.set_state(user_id, "POST_EDITOR", state_data)
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_discard$"))
async def discard_post_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        # Delete downloaded files
        for file in state_data.get("media_files", []):
            try:
                if os.path.exists(file):
                    os.remove(file)
            except Exception:
                pass
        await UserState.clear_state(user_id)
        
    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        is_admin = user.role == "admin"
        
    await callback_query.message.edit_text(
        "🗑️ **Post Discarded.**\n\nAll downloaded media files were deleted.",
        reply_markup=builders.get_home_keyboard(is_admin)
    )
    await callback_query.answer("Post discarded.")


# --- Publishing Logic ---

async def process_and_prepare_files(user_id: int, files: list[str]) -> list[str]:
    """Applies crop, resize/compress, and watermarks to files, returning paths to processed copies."""
    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        settings = user.settings or {}
        watermark_enabled = settings.get("watermark_enabled", False)
        watermark_text = settings.get("watermark_text", "")
        crop_mode = settings.get("crop_mode", "none")

    processed_paths = []
    
    for idx, raw_path in enumerate(files):
        # 1. Fit aspect ratio if needed
        if crop_mode != "none":
            target = 1.0 if crop_mode == "1:1" else 0.8
            # In-place crop/pad or save as copy?
            fit_path = fit_aspect_ratio(raw_path, mode="pad", target_ratio=target)
        else:
            fit_path = raw_path

        # 2. Watermark if enabled
        if watermark_enabled and watermark_text:
            wm_path = apply_watermark(fit_path, watermark_text)
            # Delete intermediate crop file if it was created
            if fit_path != raw_path and os.path.exists(fit_path):
                try:
                    os.remove(fit_path)
                except Exception:
                    pass
            active_path = wm_path
        else:
            active_path = fit_path

        # 3. Resize and compress to match limits
        final_path = os.path.join(DOWNLOADS_DIR, f"final_{user_id}_{idx}_{datetime.now().timestamp()}.jpg")
        resize_and_compress(active_path, final_path)
        
        # Cleanup temporary intermediate files
        if active_path != raw_path and os.path.exists(active_path):
            try:
                os.remove(active_path)
            except Exception:
                pass
                
        processed_paths.append(final_path)
        
    return processed_paths


@Client.on_callback_query(filters.regex("^post_publish_now$"))
async def publish_now_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if not state_data:
        await callback_query.answer("⚠️ Post context expired! Please upload the photo again.", show_alert=True)
        return

    chat_id = callback_query.message.chat.id
    status_msg = await callback_query.message.reply_text("⚙️ **Initiating post upload flow...**")
    await callback_query.answer()

    # Callback helper to display uploading progress steps in Telegram
    async def upload_progress(status_text: str):
        try:
            await status_msg.edit_text(f"⚡ **Posting to Instagram**\n\nStatus: {status_text}")
        except Exception:
            pass

    try:
        # Validate connection
        async with async_session() as db:
            account = await crud.get_instagram_account(db)
            if not account:
                await status_msg.edit_text(
                    "❌ **Connection Error!**\n\nNo Instagram account is connected. "
                    "Please go to **Settings & Account** -> **Connect Instagram** first."
                )
                return

        # 1. Process files (Resize, Aspect Fit, Watermark)
        await upload_progress("🎨 Preparing images (cropping, watermarking, resizing)...")
        raw_files = state_data.get("media_files", [])
        
        processed_files = await process_and_prepare_files(user_id, raw_files)
        
        # Verify sizes/formats
        for p in processed_files:
            valid, err = validate_image(p)
            if not valid:
                raise ValueError(f"Image validation failed: {err}")

        # 2. Save Draft Post in Database
        async with async_session() as db:
            post = await crud.create_post(
                db=db,
                user_id=user_id,
                media_files=processed_files,
                media_type=state_data.get("media_type", "image"),
                caption=state_data.get("caption", ""),
                status="draft"
            )

        # 3. Publish to Instagram via InstagramService
        async with async_session() as db:
            # reload post inside session
            db_post = await crud.get_post(db, post.id)
            success, result = await instagram_service.post_to_instagram(
                db=db,
                post=db_post,
                progress_callback=upload_progress
            )

        if success:
            await status_msg.edit_text(
                "✅ **Successfully Posted to Instagram!**\n\n"
                f"🔗 [View on Instagram]({result})",
                disable_web_page_preview=False
            )
            # Cleanup downloaded files
            for file in raw_files + processed_files:
                try:
                    if os.path.exists(file):
                        os.remove(file)
                except Exception:
                    pass
            await UserState.clear_state(user_id)
        else:
            # Result contains error details
            await status_msg.edit_text(
                "❌ **Failed to Post to Instagram!**\n\n"
                f"Error details:\n`{result}`"
            )
            # Delete processed files but keep raw_files in state so they can retry
            for file in processed_files:
                try:
                    if os.path.exists(file):
                        os.remove(file)
                except Exception:
                    pass
            
    except Exception as e:
        logger.error("Error during publishing: %s", e)
        await status_msg.edit_text(f"❌ **An unexpected error occurred:**\n`{str(e)}`")
