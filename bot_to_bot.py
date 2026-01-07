# app.py
import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# ================= LOAD ENV =================
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE = os.getenv("PHONE_NUMBER")

# IDs must be NUMERIC
SOURCE_CHAT_ID = int(os.getenv("PRIVATE_GROUP_ID"))
TARGET_BOT_ID = int(os.getenv("BOT_ID"))

# Session file name (Render Secret File)
SESSION_NAME = "forwarder_user"

# ================= CLIENT =================
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

async def main():
    print(" Starting Telegram Forwarder")

    try:
        # Uses existing session file (NO OTP on Render)
        await client.start(phone=PHONE)
    except SessionPasswordNeededError:
        print(" Two-step verification enabled. Disable it or use StringSession.")
        return

    print(" Telegram client connected")

    # Resolve entities ONCE (important for cloud hosting)
    try:
        source_chat = await client.get_entity(SOURCE_CHAT_ID)
        target_bot = await client.get_entity(TARGET_BOT_ID)
    except Exception as e:
        print(f" Entity resolve failed: {e}")
        return

    print(f" Monitoring chat : {source_chat.title}")
    print(f" Forwarding to bot ID : {TARGET_BOT_ID}")
    print(" Waiting for messages...\n")

    # ---------- NEW MESSAGES ----------
    @client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
    async def on_new_message(event):
        try:
            await client.forward_messages(target_bot, event.message)
            print(f" Forwarded message ID {event.message.id}")
        except Exception as e:
            print(f" Forward error: {e}")

    # ---------- EDITED MESSAGES ----------
    @client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
    async def on_edit_message(event):
        try:
            await client.forward_messages(target_bot, event.message)
            print(f" Forwarded edited message ID {event.message.id}")
        except Exception as e:
            print(f" Edit forward error: {e}")

    # Keep service alive forever
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())