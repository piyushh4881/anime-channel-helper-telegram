import json
from datetime import datetime
from sqlalchemy import select, update, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, InstagramAccount, Post, CaptionTemplate, HashtagPreset, SystemSettings, Log
from config import Config


# --- User CRUD ---
async def get_user(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_or_create_user(db: AsyncSession, user_id: int, username: str = None) -> User:
    user = await get_user(db, user_id)
    if not user:
        role = "admin" if user_id in Config.ADMINS else "user"
        user = User(id=user_id, username=username, role=role, settings={
            "watermark_enabled": False,
            "watermark_text": f"@{username}" if username else "",
            "crop_mode": "none",
            "language": "en"
        })
        db.add(user)
        await db.commit()
        await db.refresh(user)
        await add_log(db, user_id, "user_registered", f"Role: {role}")
    else:
        # Keep username updated
        if username and user.username != username:
            user.username = username
            await db.commit()
            await db.refresh(user)
    return user


async def update_user_state(db: AsyncSession, user_id: int, state: str | None, state_data: dict = None) -> User | None:
    user = await get_user(db, user_id)
    if user:
        user.state = state
        if state_data is not None:
            user.state_data = state_data
        elif state is None:
            user.state_data = None
        await db.commit()
        await db.refresh(user)
    return user


async def update_user_settings(db: AsyncSession, user_id: int, settings: dict) -> User | None:
    user = await get_user(db, user_id)
    if user:
        current_settings = dict(user.settings or {})
        current_settings.update(settings)
        user.settings = current_settings
        await db.commit()
        await db.refresh(user)
    return user


async def ban_user(db: AsyncSession, user_id: int, ban: bool = True) -> bool:
    user = await get_user(db, user_id)
    if user:
        user.status = "banned" if ban else "active"
        await db.commit()
        await add_log(db, None, "user_ban_status_changed", f"User {user_id} set to {'banned' if ban else 'active'}")
        return True
    return False


async def get_all_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User))
    return list(result.scalars().all())


# --- Instagram Account CRUD ---
async def get_instagram_account(db: AsyncSession, username: str = None) -> InstagramAccount | None:
    if username:
        result = await db.execute(select(InstagramAccount).where(InstagramAccount.username == username))
    else:
        result = await db.execute(select(InstagramAccount).where(InstagramAccount.is_active == True).limit(1))
    return result.scalar_one_or_none()


async def save_instagram_account(
    db: AsyncSession, username: str, connection_type: str, credentials: dict
) -> InstagramAccount:
    # Check if exists
    account = await get_instagram_account(db, username)
    if account:
        account.connection_type = connection_type
        account.credentials = credentials
        account.is_active = True
    else:
        # Disable other accounts
        await db.execute(update(InstagramAccount).values(is_active=False))
        account = InstagramAccount(
            username=username,
            connection_type=connection_type,
            credentials=credentials,
            is_active=True
        )
        db.add(account)
    
    await db.commit()
    await db.refresh(account)
    await add_log(db, None, "instagram_account_saved", f"Account: {username}, Type: {connection_type}")
    return account


async def remove_instagram_account(db: AsyncSession, username: str) -> bool:
    result = await db.execute(delete(InstagramAccount).where(InstagramAccount.username == username))
    await db.commit()
    return result.rowcount > 0


# --- Post CRUD ---
async def create_post(
    db: AsyncSession,
    user_id: int,
    media_files: list[str],
    media_type: str,
    caption: str = None,
    status: str = "draft",
    scheduled_at: datetime = None
) -> Post:
    post = Post(
        user_id=user_id,
        media_files=media_files,
        media_type=media_type,
        caption=caption,
        status=status,
        scheduled_at=scheduled_at
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post


async def get_post(db: AsyncSession, post_id: int) -> Post | None:
    result = await db.execute(select(Post).where(Post.id == post_id))
    return result.scalar_one_or_none()


async def update_post(db: AsyncSession, post_id: int, **kwargs) -> Post | None:
    post = await get_post(db, post_id)
    if post:
        for k, v in kwargs.items():
            setattr(post, k, v)
        await db.commit()
        await db.refresh(post)
    return post


async def get_pending_posts(db: AsyncSession) -> list[Post]:
    # Select scheduled posts that are due
    now = datetime.now()
    result = await db.execute(
        select(Post)
        .where(Post.status == "pending")
        .where(Post.scheduled_at <= now)
        .order_by(Post.scheduled_at)
    )
    return list(result.scalars().all())


async def get_upload_history(db: AsyncSession, limit: int = 50) -> list[Post]:
    result = await db.execute(
        select(Post)
        .order_by(desc(Post.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_user_posts(db: AsyncSession, user_id: int, limit: int = 50) -> list[Post]:
    result = await db.execute(
        select(Post)
        .where(Post.user_id == user_id)
        .order_by(desc(Post.created_at))
        .limit(limit)
    )
    return list(result.scalars().all())


# --- Caption Templates & Hashtag Presets CRUD ---
async def add_caption_template(db: AsyncSession, user_id: int, name: str, text: str) -> CaptionTemplate:
    template = CaptionTemplate(user_id=user_id, name=name, text=text)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


async def get_caption_templates(db: AsyncSession, user_id: int) -> list[CaptionTemplate]:
    result = await db.execute(select(CaptionTemplate).where(CaptionTemplate.user_id == user_id))
    return list(result.scalars().all())


async def delete_caption_template(db: AsyncSession, template_id: int, user_id: int) -> bool:
    result = await db.execute(
        delete(CaptionTemplate)
        .where(CaptionTemplate.id == template_id)
        .where(CaptionTemplate.user_id == user_id)
    )
    await db.commit()
    return result.rowcount > 0


async def add_hashtag_preset(db: AsyncSession, user_id: int, name: str, hashtags: str) -> HashtagPreset:
    # clean hashtags format if needed
    cleaned = " ".join([h.strip() if h.strip().startswith("#") else f"#{h.strip()}" for h in hashtags.split(" ") if h.strip()])
    preset = HashtagPreset(user_id=user_id, name=name, hashtags=cleaned)
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return preset


async def get_hashtag_presets(db: AsyncSession, user_id: int) -> list[HashtagPreset]:
    result = await db.execute(select(HashtagPreset).where(HashtagPreset.user_id == user_id))
    return list(result.scalars().all())


async def delete_hashtag_preset(db: AsyncSession, preset_id: int, user_id: int) -> bool:
    result = await db.execute(
        delete(HashtagPreset)
        .where(HashtagPreset.id == preset_id)
        .where(HashtagPreset.user_id == user_id)
    )
    await db.commit()
    return result.rowcount > 0


# --- System Settings CRUD ---
async def get_system_setting(db: AsyncSession, key: str, default=None):
    result = await db.execute(select(SystemSettings).where(SystemSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        return setting.value
    return default


async def set_system_setting(db: AsyncSession, key: str, value) -> None:
    result = await db.execute(select(SystemSettings).where(SystemSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = SystemSettings(key=key, value=value)
        db.add(setting)
    await db.commit()


# --- Logs CRUD ---
async def add_log(db: AsyncSession, user_id: int | None, action: str, details: str = None) -> Log:
    log = Log(user_id=user_id, action=action, details=details)
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def get_logs(db: AsyncSession, limit: int = 100) -> list[Log]:
    result = await db.execute(select(Log).order_by(desc(Log.created_at)).limit(limit))
    return list(result.scalars().all())


# --- Statistics Dashboard ---
async def get_statistics(db: AsyncSession) -> dict:
    total_users_q = await db.execute(select(func.count(User.id)))
    total_users = total_users_q.scalar() or 0
    
    total_posts_q = await db.execute(select(func.count(Post.id)))
    total_posts = total_posts_q.scalar() or 0
    
    success_posts_q = await db.execute(select(func.count(Post.id)).where(Post.status == "success"))
    success_posts = success_posts_q.scalar() or 0
    
    failed_posts_q = await db.execute(select(func.count(Post.id)).where(Post.status == "failed"))
    failed_posts = failed_posts_q.scalar() or 0
    
    pending_posts_q = await db.execute(select(func.count(Post.id)).where(Post.status == "pending"))
    pending_posts = pending_posts_q.scalar() or 0

    ig_account = await get_instagram_account(db)
    ig_status = "Connected" if ig_account else "Disconnected"
    ig_username = ig_account.username if ig_account else "None"
    ig_mode = ig_account.connection_type if ig_account else "None"

    return {
        "total_users": total_users,
        "total_posts": total_posts,
        "success_posts": success_posts,
        "failed_posts": failed_posts,
        "pending_posts": pending_posts,
        "instagram_status": ig_status,
        "instagram_username": ig_username,
        "instagram_mode": ig_mode
    }
