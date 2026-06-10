from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_home_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Returns the main menu keyboard."""
    buttons = [
        [InlineKeyboardButton("➕ Create New Post", callback_data="menu_create_post")],
        [
            InlineKeyboardButton("📋 Presets & Templates", callback_data="menu_presets"),
            InlineKeyboardButton("📅 Scheduled Posts", callback_data="menu_scheduled"),
        ],
        [InlineKeyboardButton("⚙️ Settings & Account", callback_data="menu_settings")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("🛠️ Admin Dashboard", callback_data="menu_admin")])
    return InlineKeyboardMarkup(buttons)


def get_post_editor_keyboard(watermark_enabled: bool, watermark_text: str, crop_mode: str) -> InlineKeyboardMarkup:
    """Returns the inline keyboard for post editing options before publishing."""
    crop_display = {"none": "Original", "1:1": "Square 1:1", "4:5": "Portrait 4:5"}.get(crop_mode, "Original")
    watermark_display = "ON ✅" if watermark_enabled else "OFF ❌"
    
    buttons = [
        [
            InlineKeyboardButton("✍️ Edit Caption", callback_data="post_edit_caption"),
            InlineKeyboardButton("🏷️ Append Hashtags", callback_data="post_add_hashtags"),
        ],
        [
            InlineKeyboardButton(f"🖼️ Aspect: {crop_display}", callback_data="post_crop_mode"),
            InlineKeyboardButton(f"💧 Watermark: {watermark_display}", callback_data="post_toggle_watermark"),
        ],
        [
            InlineKeyboardButton("⚡ Load Template", callback_data="post_load_template"),
            InlineKeyboardButton("📅 Schedule Post", callback_data="post_schedule"),
        ],
        [InlineKeyboardButton("🚀 Post to Instagram Now", callback_data="post_publish_now")],
        [InlineKeyboardButton("❌ Discard Post", callback_data="post_discard")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_crop_keyboard() -> InlineKeyboardMarkup:
    """Aspect ratio selection buttons."""
    buttons = [
        [
            InlineKeyboardButton("Original (No Crop)", callback_data="crop_none"),
            InlineKeyboardButton("Square (1:1)", callback_data="crop_1:1"),
        ],
        [
            InlineKeyboardButton("Portrait (4:5)", callback_data="crop_4:5"),
        ],
        [InlineKeyboardButton("⬅️ Back to Editor", callback_data="crop_back")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_presets_keyboard() -> InlineKeyboardMarkup:
    """Templates and hashtag preset choices."""
    buttons = [
        [
            InlineKeyboardButton("🏷️ Hashtag Presets", callback_data="preset_hashtags_list"),
            InlineKeyboardButton("✍️ Caption Templates", callback_data="preset_templates_list"),
        ],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="go_home")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_preset_list_keyboard(items: list, item_type: str) -> InlineKeyboardMarkup:
    """Lists presets/templates with delete buttons and create option."""
    buttons = []
    # items is list of CaptionTemplate or HashtagPreset
    for item in items:
        buttons.append([
            InlineKeyboardButton(item.name, callback_data=f"preset_view_{item_type}_{item.id}"),
            InlineKeyboardButton("❌ Delete", callback_data=f"preset_del_{item_type}_{item.id}")
        ])
    
    buttons.append([InlineKeyboardButton("➕ Add New", callback_data=f"preset_add_{item_type}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="menu_presets")])
    return InlineKeyboardMarkup(buttons)


def get_scheduled_keyboard(posts: list) -> InlineKeyboardMarkup:
    """Lists scheduled posts."""
    buttons = []
    # posts is list of Post models
    for post in posts:
        dt_str = post.scheduled_at.strftime("%Y-%m-%d %H:%M")
        media_type_str = "🖼️ Image" if post.media_type == "image" else "📚 Carousel"
        buttons.append([
            InlineKeyboardButton(f"{media_type_str} ({dt_str})", callback_data=f"sched_view_{post.id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"sched_cancel_{post.id}")
        ])
    
    buttons.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="go_home")])
    return InlineKeyboardMarkup(buttons)


def get_settings_keyboard(ig_connected: bool, ig_username: str, watermark_enabled: bool, watermark_text: str) -> InlineKeyboardMarkup:
    """Account and watermarking configurations."""
    conn_text = f"✅ Connected: @{ig_username}" if ig_connected else "❌ Disconnected"
    watermark_display = "ON" if watermark_enabled else "OFF"
    
    buttons = [
        [InlineKeyboardButton(f"Instagram: {conn_text}", callback_data="settings_connect_ig")],
        [
            InlineKeyboardButton(f"💧 Watermark: {watermark_display}", callback_data="settings_toggle_wm"),
            InlineKeyboardButton(f"✍️ WM Text: '{watermark_text or 'None'}'", callback_data="settings_text_wm"),
        ],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="go_home")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_instagram_conn_keyboard() -> InlineKeyboardMarkup:
    """Choose how to authenticate Instagram."""
    buttons = [
        [InlineKeyboardButton("🔑 Private API (Username/Password)", callback_data="connect_private_api")],
        [InlineKeyboardButton("🌐 Graph API (Facebook OAuth)", callback_data="connect_graph_api")],
        [InlineKeyboardButton("⬅️ Back to Settings", callback_data="menu_settings")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_admin_keyboard(admin_only: bool, maintenance: bool) -> InlineKeyboardMarkup:
    """Admin operational commands."""
    ao_text = "🔒 Admin-Only: ON" if admin_only else "🔓 Admin-Only: OFF"
    m_text = "🛠️ Maintenance: ON" if maintenance else "🟢 Maintenance: OFF"
    
    buttons = [
        [
            InlineKeyboardButton(ao_text, callback_data="admin_toggle_ao"),
            InlineKeyboardButton(m_text, callback_data="admin_toggle_m"),
        ],
        [
            InlineKeyboardButton("📁 View Upload Logs", callback_data="admin_view_logs"),
            InlineKeyboardButton("📊 Stats Dashboard", callback_data="admin_view_stats"),
        ],
        [
            InlineKeyboardButton("⚙️ Manage Admins", callback_data="admin_manage_admins"),
            InlineKeyboardButton("🔄 Restart Bot", callback_data="admin_restart_bot"),
        ],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="go_home")],
    ]
    return InlineKeyboardMarkup(buttons)


def get_back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Simple back button generator."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=callback_data)]])
