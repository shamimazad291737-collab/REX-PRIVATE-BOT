import os
import re
import math
import logging
import requests
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Logging configuration
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ---------------- Config / Constants ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "123456789"))
VAK_SMS_API_KEY = os.environ.get("VAK_SMS_API_KEY", "YOUR_VAK_SMS_API_KEY")

# Conversation states
SUBMIT_TRX, ENTER_AMOUNT = range(2)

# In-memory storage (Database er poriborte temporary storage)
user_balances = {}
used_trx_ids = set()

# ---------------- Flask Web Server for Render & UptimeRobot ----------------
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "VAK-SMS Telegram Bot is Active!", 200

def run_flask():
    # Render dynamic PORT supply kore, seta na thakle default 8080 use korbe
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ---------------- Helper Functions ----------------
def get_user_balance(user_id):
    return user_balances.get(user_id, 0.0)

def update_user_balance(user_id, amount):
    current = get_user_balance(user_id)
    user_balances[user_id] = current + amount

# ---------------- Telegram Bot Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    balance = get_user_balance(user.id)
    
    keyboard = [
        [InlineKeyboardButton("📱 Buy Number (VAK-SMS)", callback_data="buy_number")],
        [InlineKeyboardButton("💳 Deposit", callback_data="deposit"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("💬 Support", url="https://t.me/your_support_username")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"👋 Hello {user.first_name}!\n\n"
        f"Welcome to VAK-SMS Services Bot.\n"
        f"💰 Your Current Balance: ${balance:.2f}"
    )
    
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "profile":
        balance = get_user_balance(user_id)
        text = f"👤 **User Profile**\n\nID: `{user_id}`\nBalance: ${balance:.2f}"
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif query.data == "back_main":
        await start(update, context)

# ---------------- Deposit Conversation Handlers ----------------
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("💸 Send TrxID for deposit verification:")
    return SUBMIT_TRX

async def receive_trx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trx_id = update.message.text.strip()
    if trx_id in used_trx_ids:
        await update.message.reply_text("❌ This TrxID has already been used.")
        return ConversationHandler.END
    
    context.user_data['trx_id'] = trx_id
    await update.message.reply_text("💵 Enter deposit amount ($):")
    return ENTER_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Deposit cancelled.")
        return ConversationHandler.END
    
    trx_id = context.user_data.get('trx_id')
    used_trx_ids.add(trx_id)
    user_id = update.effective_user.id
    
    update_user_balance(user_id, amount)
    await update.message.reply_text(f"✅ Deposit successful! Added ${amount:.2f} to your account.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

# ---------------- Main Function ----------------
def main():
    # Render/UptimeRobot health check er jonno Flask server-ke background thread-e run kora
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Telegram Bot Application Initialize
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation Handler for Deposit
    dep_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(deposit_start, pattern="^deposit$")],
        states={
            SUBMIT_TRX: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_trx)],
            ENTER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Handlers Add Kora
    app.add_handler(CommandHandler("start", start))
    app.add_handler(dep_handler)
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    print("VAK-SMS Full Bot is running and Flask Web Server active for Render...")
    
    # Telegram Bot Polling (It will block the main thread, which is fine since Flask runs in background)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
