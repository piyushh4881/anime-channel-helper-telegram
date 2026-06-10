"""One-time authentication helper.
Creates the Telethon session file so bot.py can run non-interactively.
"""
import asyncio
from telethon import TelegramClient
from config import load_config

async def authenticate():
    config = load_config()
    client = TelegramClient(config.session_name, config.api_id, config.api_hash)
    
    await client.connect()
    
    if not await client.is_user_authorized():
        phone = "+917096274881"
        print(f"Sending code to {phone}...")
        await client.send_code_request(phone)
        
        code = input("Enter the code Telegram sent you: ")
        await client.sign_in(phone, code)
    
    me = await client.get_me()
    print(f"Authenticated as: {me.first_name} (ID: {me.id})")
    print("Session file created. You can now run: python bot.py")
    
    await client.disconnect()

asyncio.run(authenticate())
