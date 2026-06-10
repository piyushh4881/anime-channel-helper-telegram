import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class InstagramGraphAPI:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def get_long_lived_token(self, client_id: str, client_secret: str, short_lived_token: str) -> dict:
        """Exchanges a short-lived token for a long-lived user access token (valid 60 days)."""
        url = f"{BASE_URL}/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "fb_exchange_token": short_lived_token,
        }
        response = await self.client.get(url, params=params)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to get long-lived token: {data.get('error', {}).get('message', 'Unknown error')}")
        return data

    async def get_instagram_accounts(self, access_token: str) -> list[dict]:
        """Gets Facebook pages and associated Instagram Business Accounts linked to the user token."""
        url = f"{BASE_URL}/me/accounts"
        params = {"fields": "instagram_business_account,name", "access_token": access_token}
        response = await self.client.get(url, params=params)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to fetch pages: {data.get('error', {}).get('message', 'Unknown error')}")
        
        accounts = []
        for page in data.get("data", []):
            ig_account = page.get("instagram_business_account")
            if ig_account:
                accounts.append({
                    "page_name": page["name"],
                    "instagram_account_id": ig_account["id"],
                })
        return accounts

    async def create_media_container(
        self, instagram_id: str, image_url: str, caption: str, access_token: str, is_carousel_item: bool = False
    ) -> str:
        """Creates a media container for a single image."""
        url = f"{BASE_URL}/{instagram_id}/media"
        payload = {
            "image_url": image_url,
            "access_token": access_token,
        }
        if is_carousel_item:
            payload["is_carousel_item"] = "true"
        elif caption:
            payload["caption"] = caption

        response = await self.client.post(url, data=payload)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to create media container: {data.get('error', {}).get('message', 'Unknown error')}")
        return data["id"]

    async def create_carousel_container(
        self, instagram_id: str, children_ids: list[str], caption: str, access_token: str
    ) -> str:
        """Creates a media container for a carousel (album) composed of multiple media items."""
        url = f"{BASE_URL}/{instagram_id}/media"
        payload = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
            "access_token": access_token,
        }
        if caption:
            payload["caption"] = caption

        response = await self.client.post(url, data=payload)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to create carousel container: {data.get('error', {}).get('message', 'Unknown error')}")
        return data["id"]

    async def wait_for_container(self, container_id: str, access_token: str, timeout: int = 120) -> None:
        """Polls the container status until it finishes processing (FINISHED) or fails (ERROR)."""
        url = f"{BASE_URL}/{container_id}"
        params = {"fields": "status_code,status", "access_token": access_token}
        
        start_time = asyncio.get_event_loop().time()
        while True:
            response = await self.client.get(url, params=params)
            data = response.json()
            if response.status_code != 200:
                raise Exception(f"Failed to query container status: {data.get('error', {}).get('message', 'Unknown error')}")
            
            status_code = data.get("status_code")
            if status_code == "FINISHED":
                return
            elif status_code == "ERROR":
                error_msg = data.get("error", "Unknown processing error")
                raise Exception(f"Instagram media processing failed: {error_msg}")
            
            if asyncio.get_event_loop().time() - start_time > timeout:
                raise TimeoutError("Timeout waiting for media container processing")
            
            await asyncio.sleep(4)

    async def publish_media(self, instagram_id: str, container_id: str, access_token: str) -> str:
        """Publishes the processed media container to Instagram."""
        url = f"{BASE_URL}/{instagram_id}/media_publish"
        payload = {
            "creation_id": container_id,
            "access_token": access_token,
        }
        response = await self.client.post(url, data=payload)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to publish media: {data.get('error', {}).get('message', 'Unknown error')}")
        return data["id"]

    async def get_permalink(self, media_id: str, access_token: str) -> str:
        """Fetches the public permalink URL of a published Instagram post."""
        url = f"{BASE_URL}/{media_id}"
        params = {"fields": "permalink", "access_token": access_token}
        response = await self.client.get(url, params=params)
        data = response.json()
        if response.status_code != 200:
            raise Exception(f"Failed to fetch permalink: {data.get('error', {}).get('message', 'Unknown error')}")
        return data["permalink"]
