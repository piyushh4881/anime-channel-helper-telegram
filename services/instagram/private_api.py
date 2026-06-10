import asyncio
import logging
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired, BadPassword, ChallengeRequired, LoginRequired

logger = logging.getLogger(__name__)


class InstagramPrivateAPI:
    def __init__(self):
        self.cl = Client()
        # Set a default timeout
        self.cl.delay_range = [1, 3]

    def _sync_login(self, username, password, session_data=None, verification_code=None) -> tuple[dict | None, str | None, bool]:
        """
        Sync login logic to be run in a thread.
        Returns (settings_dict, error_message, 2fa_required).
        """
        # Try loading session first
        if session_data:
            try:
                logger.info("Attempting to login to Instagram via cached session for %s", username)
                self.cl.set_settings(session_data)
                self.cl.login(username, password)
                # Check session health
                self.cl.get_timeline_feed()
                logger.info("Successfully authenticated %s via cached session", username)
                return self.cl.get_settings(), None, False
            except Exception as e:
                logger.warning("Cached session authentication failed for %s: %s. Retrying password login...", username, e)
                self.cl = Client()
                self.cl.delay_range = [1, 3]

        try:
            logger.info("Attempting password login for %s", username)
            if verification_code:
                self.cl.login(username, password, verification_code=verification_code)
            else:
                self.cl.login(username, password)
            
            logger.info("Successfully logged in %s", username)
            return self.cl.get_settings(), None, False
            
        except TwoFactorRequired as e:
            logger.info("2FA required for user %s", username)
            return None, "2FA_REQUIRED", True
        except BadPassword:
            logger.error("Bad password for user %s", username)
            return None, "Incorrect password.", False
        except ChallengeRequired:
            logger.error("Challenge required for user %s", username)
            return None, "Instagram requires a challenge (verification via email/SMS). Please log in on your device first.", False
        except Exception as e:
            logger.error("Login exception for user %s: %s", username, e)
            return None, str(e), False

    async def login(self, username, password, session_data=None, verification_code=None) -> tuple[dict | None, str | None, bool]:
        """Async login wrapper running in a thread."""
        return await asyncio.to_thread(
            self._sync_login, username, password, session_data, verification_code
        )

    def _sync_upload_photo(self, username, password, session_data, image_path, caption) -> tuple[str, str]:
        """Sync photo upload."""
        self.cl.set_settings(session_data)
        self.cl.login(username, password)
        
        logger.info("Uploading photo %s for %s", image_path, username)
        media = self.cl.photo_upload(image_path, caption)
        
        post_id = media.pk
        # Post URL format: https://www.instagram.com/p/CODE/
        post_url = f"https://www.instagram.com/p/{media.code}/"
        return post_id, post_url

    async def upload_photo(self, username, password, session_data, image_path, caption) -> tuple[str, str]:
        """Async photo upload wrapper."""
        return await asyncio.to_thread(
            self._sync_upload_photo, username, password, session_data, image_path, caption
        )

    def _sync_upload_album(self, username, password, session_data, image_paths, caption) -> tuple[str, str]:
        """Sync carousel album upload."""
        self.cl.set_settings(session_data)
        self.cl.login(username, password)
        
        logger.info("Uploading carousel album %s for %s", image_paths, username)
        media = self.cl.album_upload(image_paths, caption)
        
        post_id = media.pk
        post_url = f"https://www.instagram.com/p/{media.code}/"
        return post_id, post_url

    async def upload_album(self, username, password, session_data, image_paths, caption) -> tuple[str, str]:
        """Async carousel album upload wrapper."""
        return await asyncio.to_thread(
            self._sync_upload_album, username, password, session_data, image_paths, caption
        )
