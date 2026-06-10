import os
import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Post, InstagramAccount
import database.crud as crud
from services.instagram.private_api import InstagramPrivateAPI
from services.instagram.graph_api import InstagramGraphAPI
from config import Config

logger = logging.getLogger(__name__)


class InstagramService:
    def __init__(self):
        self.private_api = InstagramPrivateAPI()
        self.graph_api = InstagramGraphAPI()

    async def close(self):
        await self.graph_api.close()

    async def test_connection(self, db: AsyncSession) -> tuple[bool, str]:
        """Tests connection with the active Instagram configuration."""
        account = await crud.get_instagram_account(db)
        if not account:
            return False, "No active Instagram account connected."

        if account.connection_type == "private_api":
            try:
                username = account.username
                password = account.credentials.get("password")
                session_data = account.credentials.get("session")
                
                settings, err, is_two_fa = await self.private_api.login(username, password, session_data)
                if err:
                    if is_two_fa:
                        return False, "Authentication failed: 2FA required."
                    return False, f"Authentication failed: {err}"
                
                # Update session
                account.credentials["session"] = settings
                await db.commit()
                return True, f"Successfully connected to @{username} (Private API)."
            except Exception as e:
                return False, f"Connection failed: {str(e)}"
                
        elif account.connection_type == "graph_api":
            try:
                access_token = account.credentials.get("access_token")
                ig_id = account.credentials.get("instagram_business_account_id")
                if not access_token or not ig_id:
                    return False, "Missing Graph API credentials."
                
                # Verify access token by checking business page details
                url = f"https://graph.facebook.com/v19.0/{ig_id}"
                params = {"fields": "username,name", "access_token": access_token}
                
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(url, params=params)
                    if resp.status_code != 200:
                        return False, f"Token validation failed: {resp.json().get('error', {}).get('message')}"
                    data = resp.json()
                    return True, f"Successfully connected to @{data.get('username')} (Graph API)."
            except Exception as e:
                return False, f"Connection failed: {str(e)}"

        return False, "Unknown connection type."

    async def login_private_api(
        self, db: AsyncSession, username: str, password: str, verification_code: str = None
    ) -> tuple[bool, str, bool]:
        """Logs in to the Private API and saves credentials/session to DB."""
        # Check if we already have a session
        account = await crud.get_instagram_account(db, username)
        session_data = account.credentials.get("session") if account else None

        settings, err, two_fa_required = await self.private_api.login(
            username, password, session_data=session_data, verification_code=verification_code
        )

        if err:
            if two_fa_required:
                # Save temp credentials so we can complete login on verification code
                await crud.save_instagram_account(
                    db, username, "private_api", {"password": password, "session": None}
                )
                return False, "2FA required. Please send the verification code.", True
            return False, err, False

        # Save successful session
        await crud.save_instagram_account(
            db, username, "private_api", {"password": password, "session": settings}
        )
        return True, "Login successful.", False

    async def post_to_instagram(
        self, db: AsyncSession, post: Post, progress_callback=None
    ) -> tuple[bool, str]:
        """
        Posts media to Instagram.
        progress_callback: async callable accepting a string status message.
        """
        async def update_progress(msg: str):
            if progress_callback:
                try:
                    await progress_callback(msg)
                except Exception as ex:
                    logger.warning("Failed to run progress callback: %s", ex)

        # 1. Fetch connected account
        account = await crud.get_instagram_account(db)
        if not account:
            return False, "No active Instagram account connected. Please connect an account first."

        # Update post status to publishing
        await crud.update_post(db, post.id, status="publishing")

        try:
            # --- Private API Mode ---
            if account.connection_type == "private_api":
                await update_progress("🔑 Authenticating with Instagram Private API...")
                username = account.username
                password = account.credentials.get("password")
                session_data = account.credentials.get("session")

                settings, err, is_two_fa = await self.private_api.login(username, password, session_data)
                if err:
                    raise Exception(f"Login failed: {'2FA required' if is_two_fa else err}")

                # Save updated session settings
                account.credentials["session"] = settings
                db.add(account)
                await db.commit()

                # Get local absolute file paths
                local_paths = post.media_files
                if not local_paths:
                    raise ValueError("No media files found to post.")

                # Ensure all paths exist
                for path in local_paths:
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"Media file not found: {path}")

                if post.media_type == "image":
                    await update_progress("📤 Uploading photo to Instagram...")
                    post_id, post_url = await self.private_api.upload_photo(
                        username, password, settings, local_paths[0], post.caption or ""
                    )
                elif post.media_type == "carousel":
                    await update_progress("📤 Uploading carousel album to Instagram...")
                    post_id, post_url = await self.private_api.upload_album(
                        username, password, settings, local_paths, post.caption or ""
                    )
                else:
                    raise ValueError(f"Unsupported media type: {post.media_type}")

                # Success
                await crud.update_post(
                    db,
                    post.id,
                    status="success",
                    instagram_post_id=str(post_id),
                    instagram_link=post_url,
                    published_at=datetime.now()
                )
                await crud.add_log(db, post.user_id, "instagram_post_success", f"Post ID: {post_id}")
                return True, post_url

            # --- Graph API Mode ---
            elif account.connection_type == "graph_api":
                await update_progress("🔑 Authenticating with Instagram Graph API...")
                access_token = account.credentials.get("access_token")
                ig_id = account.credentials.get("instagram_business_account_id")
                if not access_token or not ig_id:
                    raise ValueError("Graph API credentials are not configured properly.")

                # Map local files to public URLs served by our FastAPI endpoint
                # Local path: a:/telegr/downloads/file_xyz.jpg
                # Server URL: PUBLIC_URL/media/file_xyz.jpg
                public_urls = []
                for local_path in post.media_files:
                    filename = os.path.basename(local_path)
                    public_url = f"{Config.PUBLIC_URL}/media/{filename}"
                    public_urls.append(public_url)

                logger.info("Graph API public URLs: %s", public_urls)

                if post.media_type == "image":
                    await update_progress("📤 Creating media container...")
                    container_id = await self.graph_api.create_media_container(
                        ig_id, public_urls[0], post.caption or "", access_token
                    )
                    
                    await update_progress("⏳ Instagram is processing media...")
                    await self.graph_api.wait_for_container(container_id, access_token)
                    
                    await update_progress("🚀 Publishing post...")
                    media_id = await self.graph_api.publish_media(ig_id, container_id, access_token)

                elif post.media_type == "carousel":
                    await update_progress("📤 Creating carousel item containers...")
                    item_ids = []
                    for idx, url in enumerate(public_urls):
                        await update_progress(f"📤 Uploading item {idx+1}/{len(public_urls)}...")
                        item_id = await self.graph_api.create_media_container(
                            ig_id, url, "", access_token, is_carousel_item=True
                        )
                        item_ids.append(item_id)

                    await update_progress("⏳ Instagram is processing carousel items...")
                    # Wait for all items
                    for item_id in item_ids:
                        await self.graph_api.wait_for_container(item_id, access_token)

                    await update_progress("📦 Assembling carousel album...")
                    container_id = await self.graph_api.create_carousel_container(
                        ig_id, item_ids, post.caption or "", access_token
                    )
                    
                    await update_progress("⏳ Instagram is processing carousel...")
                    await self.graph_api.wait_for_container(container_id, access_token)
                    
                    await update_progress("🚀 Publishing carousel post...")
                    media_id = await self.graph_api.publish_media(ig_id, container_id, access_token)

                else:
                    raise ValueError(f"Unsupported media type: {post.media_type}")

                await update_progress("🔗 Fetching permalink...")
                permalink = await self.graph_api.get_permalink(media_id, access_token)

                # Success
                await crud.update_post(
                    db,
                    post.id,
                    status="success",
                    instagram_post_id=str(media_id),
                    instagram_link=permalink,
                    published_at=datetime.now()
                )
                await crud.add_log(db, post.user_id, "instagram_post_success", f"Post ID: {media_id}")
                return True, permalink

            else:
                raise ValueError(f"Unknown connection type: {account.connection_type}")

        except Exception as e:
            logger.error("Instagram publish failed: %s", e)
            err_msg = str(e)
            await crud.update_post(db, post.id, status="failed", error_message=err_msg)
            await crud.add_log(db, post.user_id, "instagram_post_failed", err_msg)
            return False, err_msg
