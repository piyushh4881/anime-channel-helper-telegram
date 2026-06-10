from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship
from database.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)  # Telegram User ID
    username = Column(String, nullable=True)
    role = Column(String, default="user")  # 'admin', 'user'
    status = Column(String, default="active")  # 'active', 'banned'
    
    # Simple FSM implementation fields
    state = Column(String, nullable=True)
    state_data = Column(JSON, nullable=True)
    
    # User preferences
    settings = Column(JSON, default=lambda: {
        "watermark_enabled": False,
        "watermark_text": "",
        "crop_mode": "none",  # 'none', '1:1', '4:5'
        "language": "en"
    })
    
    created_at = Column(DateTime, default=func.now())

    posts = relationship("Post", back_populates="user", cascade="all, delete-orphan")


class InstagramAccount(Base):
    __tablename__ = "instagram_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, nullable=False)
    connection_type = Column(String, nullable=False)  # 'private_api', 'graph_api'
    
    # Store tokens, passwords, cookies safely
    credentials = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    caption = Column(String, nullable=True)
    media_type = Column(String, nullable=False)  # 'image', 'carousel', 'video'
    
    # JSON list of local file paths (or URLs)
    media_files = Column(JSON, nullable=False, default=list)
    
    # Instagram details after successful upload
    instagram_post_id = Column(String, nullable=True)
    instagram_link = Column(String, nullable=True)
    
    status = Column(String, default="pending")  # 'draft', 'pending', 'publishing', 'success', 'failed'
    error_message = Column(String, nullable=True)
    
    scheduled_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="posts")


class CaptionTemplate(Base):
    __tablename__ = "caption_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    text = Column(String, nullable=False)


class HashtagPreset(Base):
    __tablename__ = "hashtag_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    hashtags = Column(String, nullable=False)  # Space-separated list of hashtags


class SystemSettings(Base):
    __tablename__ = "system_settings"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=False)


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action = Column(String, nullable=False)
    details = Column(String, nullable=True)
    created_at = Column(DateTime, default=func.now())
