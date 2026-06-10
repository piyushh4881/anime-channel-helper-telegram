import asyncio
import sys
sys.path.append('.')
from config import load_config
from telethon import TelegramClient
from caption_processor import clean_entities

async def main():
    config = load_config()
    client = TelegramClient(
        config.session_name,
        config.api_id,
        config.api_hash,
    )
    await client.start()
    
    dest_chat_id = -1003906919029
    msg_id = 25252
    
    try:
        message = await client.get_messages(dest_chat_id, ids=msg_id)
        if not message:
            print(f"Message {msg_id} not found in channel {dest_chat_id}")
            return
            
        print("--- Original Message Text ---")
        print(repr(message.text))
        print("--- Original Entities ---")
        print(message.entities)
        
        if message.text:
            cleaned_text, cleaned_entities = clean_entities(
                message.text, list(message.entities) if message.entities else None
            )
            print("--- Cleaned Message Text ---")
            print(repr(cleaned_text))
            
            if cleaned_text != message.text:
                print("Captions differ! Editing message...")
                await client.edit_message(
                    entity=dest_chat_id,
                    message=msg_id,
                    text=cleaned_text or "",
                    formatting_entities=cleaned_entities,
                    parse_mode=None
                )
                print("Message edited successfully!")
            else:
                print("No change needed. Caption is already clean.")
        else:
            print("Message has no text.")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
