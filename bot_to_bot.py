import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ============ LOAD ENV ============
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")

SOURCE_CHAT_ID = int(os.getenv("PRIVATE_GROUP_ID"))
TARGET_BOT = os.getenv("TARGET_BOT_USERNAME")

# ============ CLIENT ============
client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH
)

async def main():
    print("🚀 Telegram All-In-One Sender Started")

    await client.start()
    print("✅ Client connected")

    source = await client.get_entity(SOURCE_CHAT_ID)
    print(f"👀 Monitoring: {source.title}")
    print(f"📨 Sending everything to: {TARGET_BOT}")
    print("📡 Waiting for messages...\n")

    # ========= NEW MESSAGES =========
    @client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
    async def on_new_message(event):
        try:
            msg = event.message

            # TEXT (send → best for bots)
            if msg.text and not msg.media:
                await client.send_message(
                    TARGET_BOT,
                    msg.text
                )
                print(f"✅ Sent text | {msg.id}")

            # MEDIA / STICKER / VOICE / GIF / FILE / ALBUM
            else:
                await client.forward_messages(
                    TARGET_BOT,
                    msg
                )
                print(f"📦 Forwarded media | {msg.id}")

        except Exception as e:
            print("⚠️ Send error:", e)

    # ========= EDITED MESSAGES =========
    @client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
    async def on_edited_message(event):
        try:
            await client.forward_messages(
                TARGET_BOT,
                event.message
            )
            print(f"✏️ Forwarded edited | {event.message.id}")
        except Exception as e:
            print("⚠️ Edit error:", e)

    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())