import logging
from urllib.parse import urlencode
import httpx
from fastapi import APIRouter, Depends, Query, HTTPException, responses
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
import database.crud as crud
from services.instagram.graph_api import InstagramGraphAPI
from bot.client import app as bot_app
from config import Config

logger = logging.getLogger(__name__)
router = APIRouter()
graph_api = InstagramGraphAPI()


@router.get("/health")
async def health_check():
    """Simple health check endpoint."""
    return {"status": "ok", "bot_running": bot_app.is_connected if hasattr(bot_app, 'is_connected') else True}


@router.get("/login")
async def login_oauth():
    """Redirects the user to Facebook Login dialog for Instagram permissions."""
    if not Config.INSTAGRAM_GRAPH_CLIENT_ID or not Config.INSTAGRAM_GRAPH_REDIRECT_URI:
        raise HTTPException(
            status_code=400,
            detail="Instagram Graph API configurations are missing on the server. Please check environment variables."
        )

    params = {
        "client_id": Config.INSTAGRAM_GRAPH_CLIENT_ID,
        "redirect_uri": Config.INSTAGRAM_GRAPH_REDIRECT_URI,
        "scope": "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list",
        "response_type": "code",
        "state": "telegram_bot_connect",
    }
    facebook_oauth_url = f"https://www.facebook.com/v19.0/dialog/oauth?{urlencode(params)}"
    return responses.RedirectResponse(url=facebook_oauth_url)


@router.get("/instagram/callback")
async def instagram_callback(
    code: str = Query(None),
    error: str = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Processes the OAuth callback, exchanges tokens, and saves the connected Instagram account."""
    if error:
        logger.error("OAuth callback error: %s", error)
        return responses.HTMLResponse(
            content=f"<h2>Authentication Failed</h2><p>Error: {error}</p>",
            status_code=400
        )

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    try:
        # 1. Exchange auth code for short-lived access token
        url = "https://graph.facebook.com/v19.0/oauth/access_token"
        params = {
            "client_id": Config.INSTAGRAM_GRAPH_CLIENT_ID,
            "redirect_uri": Config.INSTAGRAM_GRAPH_REDIRECT_URI,
            "client_secret": Config.INSTAGRAM_GRAPH_CLIENT_SECRET,
            "code": code,
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params)
            resp_data = resp.json()
            if resp.status_code != 200:
                raise Exception(f"Short-lived token exchange failed: {resp_data.get('error', {}).get('message')}")
            
            short_token = resp_data["access_token"]

        # 2. Exchange short-lived token for long-lived page/user token (60 days)
        long_token_data = await graph_api.get_long_lived_token(
            Config.INSTAGRAM_GRAPH_CLIENT_ID,
            Config.INSTAGRAM_GRAPH_CLIENT_SECRET,
            short_token
        )
        long_lived_token = long_token_data["access_token"]

        # 3. Retrieve associated Instagram Business Account IDs
        linked_accounts = await graph_api.get_instagram_accounts(long_lived_token)
        if not linked_accounts:
            return responses.HTMLResponse(
                content="<h2>No Accounts Found</h2><p>Your Facebook account is not linked to any Instagram Business/Professional accounts.</p>",
                status_code=400
            )

        # Connect the first linked account found
        target_account = linked_accounts[0]
        ig_id = target_account["instagram_account_id"]
        page_name = target_account["page_name"]

        # 4. Fetch Instagram Username details using token
        ig_details_url = f"https://graph.facebook.com/v19.0/{ig_id}"
        details_params = {"fields": "username,name", "access_token": long_lived_token}
        
        async with httpx.AsyncClient() as client:
            ig_resp = await client.get(ig_details_url, params=details_params)
            ig_data = ig_resp.json()
            if ig_resp.status_code != 200:
                raise Exception(f"Failed to query IG details: {ig_data.get('error', {}).get('message')}")
            
            ig_username = ig_data["username"]

        # 5. Save credentials to Database
        credentials = {
            "access_token": long_lived_token,
            "instagram_business_account_id": ig_id,
            "facebook_page_name": page_name
        }
        await crud.save_instagram_account(db, ig_username, "graph_api", credentials)

        # 6. Notify admin(s) via Telegram
        for admin_id in Config.ADMINS:
            try:
                await bot_app.send_message(
                    chat_id=admin_id,
                    text=(
                        "🌐 **Instagram Graph API connected successfully!**\n\n"
                        f"• **Account:** @{ig_username}\n"
                        f"• **Facebook Page:** {page_name}\n"
                        "You can now post directly via official Graph API endpoints!"
                    )
                )
            except Exception as tg_err:
                logger.warning("Could not notify admin %d: %s", admin_id, tg_err)

        # Return beautiful confirmation screen
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Connection Successful</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    background-color: #fafafa;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }}
                .container {{
                    background: white;
                    padding: 40px;
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 400px;
                }}
                h1 {{ color: #4F46E5; font-size: 24px; margin-bottom: 10px; }}
                p {{ color: #4B5563; line-height: 1.5; font-size: 16px; }}
                .username {{ font-weight: bold; color: #111827; }}
                .badge {{
                    background-color: #EEF2F6;
                    color: #4F46E5;
                    padding: 8px 16px;
                    border-radius: 20px;
                    font-size: 14px;
                    display: inline-block;
                    margin-top: 15px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>✅ Instagram Connected!</h1>
                <p>The account <span class="username">@{ig_username}</span> has been linked successfully.</p>
                <div class="badge">Graph API Integration</div>
                <p style="margin-top: 25px; font-size: 14px; color: #9CA3AF;">You can now close this window and return to Telegram.</p>
            </div>
        </body>
        </html>
        """
        return responses.HTMLResponse(content=html_content)

    except Exception as e:
        logger.error("OAuth token exchange exception: %s", e)
        return responses.HTMLResponse(
            content=f"<h2>Integration Failed</h2><p>Error description: {str(e)}</p>",
            status_code=500
        )
