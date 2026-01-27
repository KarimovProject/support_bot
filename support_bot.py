# support_bot.py
"""
Support Bot — with ✉️ Reply button for admin response
"""

import os
import re
import sqlite3
import asyncio
import logging
from datetime import datetime
from html import escape as html_escape

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# --- CONFIG ---
BOT_TOKEN = ""
ADMIN_IDS = [1451992690]
ADMIN_CHAT_IDS = [int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip()]
DB_PATH = os.environ.get("SUPPORT_DB", "support_bot.db")

if not BOT_TOKEN:
    raise RuntimeError("Please set BOT_TOKEN environment variable.")

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- In-memory mappings ---
ADMIN_MSG_TO_TICKET = {}   # (chat_id, msg_id) → ticket_id
pending_replies = {}       # admin_id → ticket_id (reply jarayoni)

# --- Database helpers ---
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            user_username TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            from_admin INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            text TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    return conn

DB_CONN = init_db()

async def db_execute(query, params=(), commit=False, fetch=False):
    loop = asyncio.get_running_loop()
    def _exec():
        cur = DB_CONN.cursor()
        cur.execute(query, params)
        res = None
        if fetch:
            res = cur.fetchall()
        if commit:
            DB_CONN.commit()
        return res, cur.lastrowid
    res, lastrow = await loop.run_in_executor(None, _exec)
    return res, lastrow

# --- Utils ---
async def create_ticket(user, username, first_name):
    now = datetime.utcnow().isoformat()
    q = "INSERT INTO tickets (user_id, user_name, user_username, created_at) VALUES (?, ?, ?, ?)"
    _, new_id = await db_execute(q, (user, first_name, username or "", now), commit=True)
    return new_id

async def get_ticket(ticket_id):
    res, _ = await db_execute(
        "SELECT id, user_id, user_name, user_username, status FROM tickets WHERE id = ?",
        (ticket_id,),
        fetch=True,
    )
    if res:
        r = res[0]
        return {"id": r[0], "user_id": r[1], "user_name": r[2], "user_username": r[3], "status": r[4]}
    return None

async def log_message(ticket_id, from_admin, chat_id, message_id, text):
    now = datetime.utcnow().isoformat()
    await db_execute(
        "INSERT INTO messages (ticket_id, from_admin, chat_id, message_id, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ticket_id, 1 if from_admin else 0, chat_id, message_id, text or "", now),
        commit=True,
    )

# --- Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Assalomu alaykum! Support botga xush kelibsiz.\n"
        "Siz bu bot orqali muammoingizni yozing — adminlar bilan bogʻlanasiz."
    )

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cid = update.effective_chat.id
    await update.message.reply_text(f"Your user.id = {uid}\nThis chat.id = {cid}")

# --- USER MESSAGE ---
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = msg.from_user
    ticket_id = await create_ticket(user.id, user.username, user.first_name)
    await log_message(ticket_id, from_admin=False, chat_id=user.id, message_id=msg.message_id, text=msg.text or msg.caption or "")

    safe_name = html_escape(user.first_name or "—")
    safe_username = html_escape(user.username or "—")
    safe_text = html_escape(msg.text or msg.caption or "")

    header_text = (
        f"🎫 <b>TicketID:</b> {ticket_id}\n"
        f"<b>From:</b> {safe_name} (@{safe_username})\n"
        f"<b>UserID:</b> {user.id}\n\n"
        f"<b>Xabar:</b>\n<pre>{safe_text}</pre>"
    )

    # ✅ To‘g‘rilangan inline tugma
    reply_button = InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Reply", callback_data=f"reply_{ticket_id}")]])

    for admin_id in set(ADMIN_IDS) | set(ADMIN_CHAT_IDS):
        header = await context.bot.send_message(admin_id, header_text, parse_mode="HTML", reply_markup=reply_button)
        ADMIN_MSG_TO_TICKET[(admin_id, header.message_id)] = ticket_id

    await msg.reply_text("✅ So‘rovingiz adminga yuborildi. Javobni shu yerda kuting.")

# --- ADMIN: tugma bosilganda ---
async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id
    ticket_id = int(query.data.split("_")[1])

    pending_replies[admin_id] = ticket_id
    await query.answer()
    await query.message.reply_text(f"✏️ Endi Ticket #{ticket_id} uchun foydalanuvchiga javob yozing.")

# --- ADMIN: reply jarayonida yozgan xabar ---
async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    admin_id = msg.from_user.id

    if admin_id not in ADMIN_IDS:
        return

    if admin_id in pending_replies:
        ticket_id = pending_replies[admin_id]
        ticket = await get_ticket(ticket_id)
        if not ticket:
            await msg.reply_text("⚠️ Ticket topilmadi.")
            return
        user_id = ticket["user_id"]

        await context.bot.send_message(user_id, f"👨‍💼 Admin javobi (Ticket #{ticket_id}):\n\n{msg.text}")
        await msg.reply_text(f"✅ Javob foydalanuvchiga yuborildi (Ticket #{ticket_id}).")

        await log_message(ticket_id, True, msg.chat.id, msg.message_id, msg.text)
        await db_execute("UPDATE tickets SET status = ? WHERE id = ?", ("answered", ticket_id), commit=True)

        del pending_replies[admin_id]
    else:
        await msg.reply_text("ℹ️ Javob berish uchun ✉️ Reply tugmasini bosing.")

# --- MAIN ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(CallbackQueryHandler(handle_reply_button, pattern=r"^reply_\d+$"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (~filters.COMMAND), handle_user_message))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), handle_admin_message))
    logger.info("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
