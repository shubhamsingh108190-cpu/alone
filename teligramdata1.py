import os
import asyncio
import sqlite3
from datetime import datetime
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    filters,
)

# ================== ENV VARIABLES ==================
BOT_A_TOKEN = os.getenv("BOT_A_TOKEN")          # Admin bot (source)
BOT_B_TOKEN = os.getenv("BOT_B_TOKEN")          # Receiver bot
BOT_B_CHAT_ID = int(os.getenv("BOT_B_CHAT_ID")) # Where data is sent

# Render provides this automatically
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
# ==================================================

if not all([BOT_A_TOKEN, BOT_B_TOKEN, BOT_B_CHAT_ID, WEBHOOK_URL]):
    raise RuntimeError("Missing environment variables")

# ================== DATABASE ==================
DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            message_type TEXT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            group_id INTEGER,
            group_title TEXT,
            content TEXT,
            file_id TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_activity(event_type, message_type, user, chat, content=None, file_id=None):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO activities (
            event_type, message_type,
            user_id, username, full_name,
            group_id, group_title,
            content, file_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_type,
        message_type,
        user.id if user else None,
        user.username if user else None,
        user.full_name if user else None,
        chat.id if chat else None,
        chat.title if chat else None,
        content,
        file_id,
        datetime.utcnow().isoformat()
    ))
    conn.commit()
    conn.close()

# ================== TELEGRAM ==================
bot_b = Bot(BOT_B_TOKEN)
flask_app = Flask(__name__)
tg_app: Application = ApplicationBuilder().token(BOT_A_TOKEN).build()

# ================== HELPERS ==================
async def send_text(text):
    await bot_b.send_message(
        chat_id=BOT_B_CHAT_ID,
        text=text,
        disable_web_page_preview=False
    )

async def forward_media(msg, media_type, file_id):
    save_activity(
        event_type="message",
        message_type=media_type,
        user=msg.from_user,
        chat=msg.chat,
        file_id=file_id
    )

    caption = (
        f"📦 MEDIA\n"
        f"👤 {msg.from_user.full_name}\n"
        f"💬 {msg.chat.title}"
    )

    if media_type == "photo":
        await bot_b.send_photo(BOT_B_CHAT_ID, file_id, caption=caption)
    elif media_type == "video":
        await bot_b.send_video(BOT_B_CHAT_ID, file_id, caption=caption)
    elif media_type == "document":
        await bot_b.send_document(BOT_B_CHAT_ID, file_id, caption=caption)
    elif media_type == "audio":
        await bot_b.send_audio(BOT_B_CHAT_ID, file_id, caption=caption)
    elif media_type == "voice":
        await bot_b.send_voice(BOT_B_CHAT_ID, file_id, caption=caption)
    elif media_type == "sticker":
        await bot_b.send_sticker(BOT_B_CHAT_ID, file_id)

# ================== HANDLERS ==================
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.from_user:
        return

    # TEXT / LINKS / CAPTIONS
    if msg.text or msg.caption:
        content = msg.text or msg.caption
        save_activity("message", "text", msg.from_user, msg.chat, content=content)

        await send_text(
            f"💬 MESSAGE\n"
            f"👤 {msg.from_user.full_name}\n"
            f"💬 {msg.chat.title}\n\n"
            f"{content}"
        )

    # MEDIA
    if msg.photo:
        await forward_media(msg, "photo", msg.photo[-1].file_id)
    elif msg.video:
        await forward_media(msg, "video", msg.video.file_id)
    elif msg.document:
        await forward_media(msg, "document", msg.document.file_id)
    elif msg.audio:
        await forward_media(msg, "audio", msg.audio.file_id)
    elif msg.voice:
        await forward_media(msg, "voice", msg.voice.file_id)
    elif msg.sticker:
        await forward_media(msg, "sticker", msg.sticker.file_id)

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.edited_message
    if not msg or not msg.text:
        return

    save_activity("edit", "text", msg.from_user, msg.chat, content=msg.text)
    await send_text(f"✏️ EDITED MESSAGE\n{msg.text}")

async def handle_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.chat_member
    user = m.new_chat_member.user

    save_activity(
        "member_update",
        m.new_chat_member.status,
        user,
        m.chat
    )

    await send_text(
        f"👥 MEMBER UPDATE\n"
        f"{user.full_name} → {m.new_chat_member.status}"
    )

# ================== REGISTER ==================
tg_app.add_handler(MessageHandler(filters.ALL, handle_messages))
tg_app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
tg_app.add_handler(ChatMemberHandler(handle_members, ChatMemberHandler.CHAT_MEMBER))

# ================== FLASK ==================
@flask_app.route("/", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    await tg_app.process_update(update)
    return "ok"

@flask_app.route("/", methods=["GET"])
def health():
    return "Bot is running"

# ================== STARTUP ==================
async def startup():
    init_db()
    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/")
    await tg_app.start()
    print("✅ Webhook set & database ready")

asyncio.get_event_loop().run_until_complete(startup())