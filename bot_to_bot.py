import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web

# ============ LOAD ENV ============
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
STRING_SESSION = os.getenv("STRING_SESSION")

SOURCE_CHAT_ID = int(os.getenv("PRIVATE_GROUP_ID"))
TARGET_BOT = os.getenv("TARGET_BOT_USERNAME")

PORT = int(os.getenv("PORT", 10000))

if not TARGET_BOT:
    raise RuntimeError("TARGET_BOT_USERNAME is missing")

# ============ TELEGRAM CLIENT ============
client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH
)

# ============ TELETHON LOGIC ============
async def telegram_worker():
    print("🚀 Telegram forwarder starting...")

    await client.start()
    print("✅ Telegram connected")

    source = await client.get_entity(SOURCE_CHAT_ID)
    print(f"👀 Monitoring: {source.title}")
    print(f"📨 Sending to: {TARGET_BOT}")

    @client.on(events.NewMessage(chats=SOURCE_CHAT_ID))
    async def on_new_message(event):
        try:
            msg = event.message

            if msg.text and not msg.media:
                await client.send_message(TARGET_BOT, msg.text)
            else:
                await client.forward_messages(TARGET_BOT, msg)

            print(f"✅ Sent message {msg.id}")

        except Exception as e:
            print("⚠️ Send error:", e)

    @client.on(events.MessageEdited(chats=SOURCE_CHAT_ID))
    async def on_edit(event):
        try:
            await client.forward_messages(TARGET_BOT, event.message)
        except Exception as e:
            print("⚠️ Edit error:", e)

    await client.run_until_disconnected()


# ============ WEB SERVER ============
async def health(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"🌐 Web server running on port {PORT}")


# ============ MAIN ============
async def main():
    await start_web()
    await telegram_worker()

if __name__ == "__main__":
    asyncio.run(main())