import asyncio
import sys
sys.path.append('.')
from config import load_config
from telethon import TelegramClient
from database import MigrationDatabase
from caption_processor import clean_entities

async def main():
    config = load_config()
    db = MigrationDatabase(config.database_path)
    await db.connect()
    
    # Get all successful migrations from database
    assert db._db is not None
    cursor = await db._db.execute(
        "SELECT source_message_id, destination_message_id FROM migrations WHERE status = 'success'"
    )
    rows = await cursor.fetchall()
    print(f"Found {len(rows)} successful migrations in database.")
    
    # Create mapping of dest_id -> src_id
    dest_to_src = {}
    for row in rows:
        src_id = row['source_message_id']
        dest_id = row['destination_message_id']
        if dest_id:
            dest_to_src[dest_id] = src_id
            
    client = TelegramClient(
        config.session_name,
        config.api_id,
        config.api_hash,
    )
    await client.start()
    
    dest_chat_id = -1003906919029
    
    edited_count = 0
    skipped_count = 0
    
    dest_ids = list(dest_to_src.keys())
    batch_size = 100
    
    for i in range(0, len(dest_ids), batch_size):
        batch = dest_ids[i:i+batch_size]
        print(f"Fetching batch of {len(batch)} messages (progress: {i}/{len(dest_ids)})...")
        try:
            messages = await client.get_messages(dest_chat_id, ids=batch)
            for message in messages:
                if not message or not message.text:
                    skipped_count += 1
                    continue
                    
                dest_id = message.id
                src_id = dest_to_src.get(dest_id)
                
                # Check if it contains "Uploaded by @HashHackers" case-insensitively
                if "uploaded by @hashhackers" not in message.text.lower():
                    skipped_count += 1
                    continue
                    
                # Clean caption
                cleaned_text, cleaned_entities = clean_entities(
                    message.text, list(message.entities) if message.entities else None
                )
                
                print(f"Editing dest_id={dest_id} (src_id={src_id})...")
                await client.edit_message(
                    entity=dest_chat_id,
                    message=dest_id,
                    text=cleaned_text or "",
                    formatting_entities=cleaned_entities,
                    parse_mode=None
                )
                edited_count += 1
                await asyncio.sleep(1.0)  # Small delay to avoid rate limit for editing
        except Exception as e:
            print(f"Error processing batch starting at index {i}: {e}")
            await asyncio.sleep(5.0)
            
    print(f"\nCleanup complete. Edited: {edited_count}, Skipped/Clean: {skipped_count}")
    await db.close()
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
