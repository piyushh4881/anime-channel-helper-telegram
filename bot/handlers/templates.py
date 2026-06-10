import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from bot.state import UserState
from database.db import async_session
import database.crud as crud

logger = logging.getLogger(__name__)

# Custom filters for template FSM states
async def template_state_filter(_, __, message: Message) -> bool:
    user_id = message.from_user.id
    state, _ = await UserState.get_state(user_id)
    return state in ("WAIT_ADD_PRESET_NAME", "WAIT_ADD_PRESET_TAGS", "WAIT_ADD_TEMPLATE_NAME", "WAIT_ADD_TEMPLATE_TEXT")

template_state = filters.create(template_state_filter)


# --- Navigation ---

@Client.on_callback_query(filters.regex("^menu_presets$"))
async def presets_menu_cb(client: Client, callback_query: CallbackQuery):
    if not await check_user_access(client, callback_query):
        return
        
    await callback_query.message.edit_text(
        "📋 **Presets & Templates Manager**\n\n"
        "Here you can manage your hashtag presets and caption templates. "
        "These can be quickly loaded or appended while editing your posts.",
        reply_markup=builders.get_presets_keyboard()
    )
    await callback_query.answer()


# --- Hashtag Presets ---

@Client.on_callback_query(filters.regex("^preset_hashtags_list$"))
async def hashtag_list_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await check_user_access(client, callback_query):
        return

    async with async_session() as db:
        presets = await crud.get_hashtag_presets(db, user_id)
        
    await callback_query.message.edit_text(
        "🏷️ **Hashtag Presets**\n\n"
        "Select a preset to view details, delete one, or add a new one:",
        reply_markup=builders.get_preset_list_keyboard(presets, "hashtags")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_add_hashtags$"))
async def add_hashtag_start_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await UserState.set_state(user_id, "WAIT_ADD_PRESET_NAME", {})
    
    await callback_query.message.edit_text(
        "➕ **Add Hashtag Preset**\n\n"
        "Please send a short name for this preset (e.g., `Summer Vibe`):",
        reply_markup=builders.get_back_button("preset_hashtags_list")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_view_hashtags_(\\d+)$"))
async def view_hashtag_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    preset_id = int(callback_query.matches[0].group(1))
    
    async with async_session() as db:
        preset = await db.get(crud.HashtagPreset, preset_id)
        
    if not preset or preset.user_id != user_id:
        await callback_query.answer("Preset not found.", show_alert=True)
        return
        
    await callback_query.message.edit_text(
        f"🏷️ **Preset: {preset.name}**\n\n"
        f"**Hashtags:**\n`{preset.hashtags}`",
        reply_markup=builders.get_back_button("preset_hashtags_list")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_del_hashtags_(\\d+)$"))
async def del_hashtag_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    preset_id = int(callback_query.matches[0].group(1))
    
    async with async_session() as db:
        success = await crud.delete_hashtag_preset(db, preset_id, user_id)
        presets = await crud.get_hashtag_presets(db, user_id)
        
    if success:
        await callback_query.answer("Preset deleted.")
        await callback_query.message.edit_text(
            "🏷️ **Hashtag Presets**\n\n"
            "Select a preset to view details, delete one, or add a new one:",
            reply_markup=builders.get_preset_list_keyboard(presets, "hashtags")
        )
    else:
        await callback_query.answer("Failed to delete preset.", show_alert=True)


# --- Caption Templates ---

@Client.on_callback_query(filters.regex("^preset_templates_list$"))
async def template_list_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await check_user_access(client, callback_query):
        return

    async with async_session() as db:
        templates = await crud.get_caption_templates(db, user_id)
        
    await callback_query.message.edit_text(
        "✍️ **Caption Templates**\n\n"
        "Select a template to view details, delete one, or add a new one:",
        reply_markup=builders.get_preset_list_keyboard(templates, "templates")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_add_templates$"))
async def add_template_start_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await UserState.set_state(user_id, "WAIT_ADD_TEMPLATE_NAME", {})
    
    await callback_query.message.edit_text(
        "➕ **Add Caption Template**\n\n"
        "Please send a name for this template (e.g., `Promo Style`):",
        reply_markup=builders.get_back_button("preset_templates_list")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_view_templates_(\\d+)$"))
async def view_template_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    template_id = int(callback_query.matches[0].group(1))
    
    async with async_session() as db:
        template = await db.get(crud.CaptionTemplate, template_id)
        
    if not template or template.user_id != user_id:
        await callback_query.answer("Template not found.", show_alert=True)
        return
        
    await callback_query.message.edit_text(
        f"✍️ **Template: {template.name}**\n\n"
        f"**Content:**\n{template.text}",
        reply_markup=builders.get_back_button("preset_templates_list")
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^preset_del_templates_(\\d+)$"))
async def del_template_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    template_id = int(callback_query.matches[0].group(1))
    
    async with async_session() as db:
        success = await crud.delete_caption_template(db, template_id, user_id)
        templates = await crud.get_caption_templates(db, user_id)
        
    if success:
        await callback_query.answer("Template deleted.")
        await callback_query.message.edit_text(
            "✍️ **Caption Templates**\n\n"
            "Select a template to view details, delete one, or add a new one:",
            reply_markup=builders.get_preset_list_keyboard(templates, "templates")
        )
    else:
        await callback_query.answer("Failed to delete template.", show_alert=True)


# --- FSM Text Inputs Handler ---

@Client.on_message(template_state & filters.text & filters.private)
async def template_text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state == "WAIT_ADD_PRESET_NAME":
        state_data["name"] = message.text.strip()
        await UserState.set_state(user_id, "WAIT_ADD_PRESET_TAGS", state_data)
        await message.reply_text(
            f"🏷️ Preset Name: **{state_data['name']}**\n\n"
            "Now send the hashtags separated by spaces (e.g., `#instadaily #photooftheday #photography`):",
            reply_markup=builders.get_back_button("preset_hashtags_list")
        )
        await message.delete()

    elif state == "WAIT_ADD_PRESET_TAGS":
        hashtags = message.text.strip()
        name = state_data["name"]
        
        async with async_session() as db:
            await crud.add_hashtag_preset(db, user_id, name, hashtags)
            presets = await crud.get_hashtag_presets(db, user_id)
            
        await UserState.clear_state(user_id)
        await message.reply_text(
            f"✅ Saved preset **{name}**!",
            reply_markup=builders.get_preset_list_keyboard(presets, "hashtags")
        )
        await message.delete()

    elif state == "WAIT_ADD_TEMPLATE_NAME":
        state_data["name"] = message.text.strip()
        await UserState.set_state(user_id, "WAIT_ADD_TEMPLATE_TEXT", state_data)
        await message.reply_text(
            f"✍️ Template Name: **{state_data['name']}**\n\n"
            "Now send the text content for the caption template:",
            reply_markup=builders.get_back_button("preset_templates_list")
        )
        await message.delete()

    elif state == "WAIT_ADD_TEMPLATE_TEXT":
        text = message.text.strip()
        name = state_data["name"]
        
        async with async_session() as db:
            await crud.add_caption_template(db, user_id, name, text)
            templates = await crud.get_caption_templates(db, user_id)
            
        await UserState.clear_state(user_id)
        await message.reply_text(
            f"✅ Saved template **{name}**!",
            reply_markup=builders.get_preset_list_keyboard(templates, "templates")
        )
        await message.delete()


# --- Load Template Flow (from Editor) ---

@Client.on_callback_query(filters.regex("^post_load_template$"))
async def post_load_template_menu_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        async with async_session() as db:
            templates = await crud.get_caption_templates(db, user_id)
            
        buttons = []
        for t in templates:
            buttons.append([InlineKeyboardButton(f"⚡ {t.name}", callback_data=f"post_apply_template_{t.id}")])
            
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="post_preview_back")])
        
        await callback_query.message.edit_text(
            "⚡ **Select a caption template to apply:**\n"
            "⚠️ *This will replace the current caption.*",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^post_apply_template_(\\d+)$"))
async def post_apply_template_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    template_id = int(callback_query.matches[0].group(1))
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        async with async_session() as db:
            template = await db.get(crud.CaptionTemplate, template_id)
            if template and template.user_id == user_id:
                # Replace caption
                state_data["caption"] = template.text
                await UserState.set_state(user_id, "POST_EDITOR", state_data)
        
        # Go back to preview editor
        # Import dynamically to avoid circular references
        from bot.handlers.post import show_editor_preview
        await show_editor_preview(client, callback_query.message.chat.id, user_id, state_data, callback_query.message.id)
    await callback_query.answer("Template applied!")
