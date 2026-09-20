import logging
import os
import threading
import asyncio
from flask import Flask
from telegram import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)
import requests

# Logging Configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Environment Variables & Config
BOT_TOKEN = os.getenv("BOT_TOKEN")
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY", "893d842ab70a4e79b4ad323185a69257")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789")) # আপনার টেলিগ্রাম ID দিন
BINANCE_ID = os.getenv("BINANCE_ID", "123456789 (Binance Pay ID)")

# Flask Web Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "VAK-SMS Telegram Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# Database / In-Memory Storage
user_selected_country = {}
user_selected_service = {}
active_orders = {}
banned_users = set()
user_balances = {} # {user_id: balance_amount}
custom_rates = {"wa": 0.10} # Default custom bot rate for WA (Admin can change)

# States for Deposit Conversation
WAITING_AMOUNT, WAITING_TXID, WAITING_SCREENSHOT = range(3)

# Keyboards
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💳 Account Balance"), KeyboardButton("🛒 Buy Number")],
        [KeyboardButton("🌐 Set Country"), KeyboardButton("📱 Set Service")],
        [KeyboardButton("📩 Check Active OTP"), KeyboardButton("❌ Cancel Number")],
        [KeyboardButton("💵 Deposit")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# VAK-SMS API Functions
def get_vak_balance():
    url = f"https://vak-sms.com/api/getBalance/?apiKey={VAK_SMS_API_KEY}"
    try:
        res = requests.get(url).json()
        return res.get("balance", 0.0)
    except Exception:
        return 0.0

def buy_vak_number(service: str, country: str):
    url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service}&country={country}&price=0.07"
    try:
        res = requests.get(url).json()
        if isinstance(res, dict) and res.get("error") == "noNumber":
            fallback_url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service}&country={country}"
            res = requests.get(fallback_url).json()
        return res
    except Exception as e:
        return {"error": str(e)}

def fetch_otp_code(id_num: str):
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}

def set_number_status(id_num: str, status: str):
    url = f"https://vak-sms.com/api/setStatus/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}&status={status}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}


# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in banned_users:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্টটি নিষিদ্ধ (Banned) করা হয়েছে।")
        return

    if user_id not in user_balances:
        user_balances[user_id] = 0.0
    if user_id not in user_selected_country:
        user_selected_country[user_id] = "hk"
    if user_id not in user_selected_service:
        user_selected_service[user_id] = "wa"

    welcome_msg = (
        f"👋 **VAK-SMS Bot-এ স্বাগতম!**\n\n"
        f"⚙️ **বর্তমান সেটআপ:**\n"
        f"• Country: `{user_selected_country[user_id].upper()}`\n"
        f"• Service: `{user_selected_service[user_id].upper()}`\n"
        f"• Bot Balance: `${user_balances[user_id]:.2f} USDT`\n\n"
        f"নিচের মেনু থেকে অপশন বেছে নিন:"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in banned_users:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্টটি নিষিদ্ধ করা হয়েছে।")
        return

    text = update.message.text.strip()

    # 1. Balance
    if text == "💳 Account Balance":
        bot_bal = user_balances.get(user_id, 0.0)
        msg = f"💰 **আপনার Bot Balance:** `${bot_bal:.2f} USDT`"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n🏦 **VAK-SMS Site Balance:** `${site_bal:.4f} USD`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # 2. Set Country
    if text == "🌐 Set Country":
        country_kb = [
            [KeyboardButton("Country: HK (Hong Kong)"), KeyboardButton("Country: US (USA)")],
            [KeyboardButton("Country: RU (Russia)"), KeyboardButton("Country: IN (India)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text("🌐 **দেশ নির্বাচন করুন:**", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if text.startswith("Country:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_country[user_id] = code
        await update.message.reply_text(f"✅ Country সেট হয়েছে: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    # 3. Set Service
    if text == "📱 Set Service":
        service_kb = [
            [KeyboardButton("Service: WA (WhatsApp)"), KeyboardButton("Service: TG (Telegram)")],
            [KeyboardButton("Service: GO (Google/Gmail)"), KeyboardButton("Service: IM (Imo)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text("📱 **সার্ভিস নির্বাচন করুন:**", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if text.startswith("Service:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_service[user_id] = code
        await update.message.reply_text(f"✅ Service সেট হয়েছে: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🔙 Main Menu":
        await start(update, context)
        return

    # 4. Buy Number
    if text == "🛒 Buy Number":
        country = user_selected_country.get(user_id, "hk")
        service = user_selected_service.get(user_id, "wa")
        bot_rate = custom_rates.get(service, 0.10)
        user_bal = user_balances.get(user_id, 0.0)

        if user_bal < bot_rate:
            await update.message.reply_text(f"❌ পর্যাপ্ত ব্যালেন্স নেই! প্রয়োজন: `${bot_rate}` USDT, আপনার আছে: `${user_bal}` USDT। দয়া করে ডিপিজিট করুন।")
            return

        await update.message.reply_text(f"⏳ `{country.upper()}` দেশের জন্য `{service.upper()}` নম্বর কেনা হচ্ছে...")

        res = buy_vak_number(service, country)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            phone_num = res["tel"]
            id_num = res["idNum"]
            active_orders[user_id] = id_num

            # Deduct Custom Bot Balance
            user_balances[user_id] -= bot_rate

            # Inline Action Buttons Right Under Number Info
            inline_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Check Active OTP", callback_data=f"check_otp_{id_num}")],
                [InlineKeyboardButton("❌ Cancel Number", callback_data=f"cancel_num_{id_num}")]
            ])

            await update.message.reply_text(
                f"✅ **নম্বর কেনা সফল হয়েছে!**\n\n"
                f"📱 **নম্বর:** `{phone_num}`\n"
                f"🆔 **ID Num:** `{id_num}`\n"
                f"🌍 **Country:** `{country.upper()}`\n"
                f"💬 **Service:** `{service.upper()}`\n"
                f"💵 **Cost:** `${bot_rate:.2f}` USDT\n",
                parse_mode="Markdown",
                reply_markup=inline_kb
            )

            asyncio.create_task(auto_check_otp(context, user_id, id_num, str(phone_num)))
        else:
            err_msg = res.get("error", "Stock Out") if isinstance(res, dict) else "Error"
            await update.message.reply_text(f"❌ **নম্বর কেনা সম্ভব হয়নি:** `{err_msg}`")
        return

    # 5. Check Active OTP
    if text == "📩 Check Active OTP":
        id_num = active_orders.get(user_id)
        if not id_num:
            await update.message.reply_text("❌ আপনার কোনো অ্যাক্টিভ নম্বর নেই।")
            return
        await manual_check_otp(update, id_num)
        return

    # 6. Cancel Number
    if text == "❌ Cancel Number":
        id_num = active_orders.get(user_id)
        if not id_num:
            await update.message.reply_text("❌ কোনো নম্বর অ্যাক্টিভ নেই।")
            return
        await manual_cancel_number(update, user_id, id_num)
        return

    # 7. Admin Panel Command
    if text == "⚙️ Admin Panel" and user_id == ADMIN_ID:
        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban")],
            [InlineKeyboardButton("💵 Set WA Bot Rate", callback_data="admin_rate"), InlineKeyboardButton("➕ Add User Balance", callback_data="admin_add_bal")]
        ])
        await update.message.reply_text("🛠 **Admin Control Panel:**", reply_markup=admin_kb)
        return


# Inline Callback Handlers
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("check_otp_"):
        id_num = data.split("_")[2]
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            await query.message.reply_text(f"🔑 **আপনার OTP কোড:** `{res['smsCode']}`", parse_mode="Markdown")
        else:
            await query.message.reply_text("⏳ এখনো OTP আসেনি, একটু পর আবার চেষ্টা করুন।")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        set_number_status(id_num, "bad")
        active_orders.pop(user_id, None)
        # Refund custom balance
        service = user_selected_service.get(user_id, "wa")
        user_balances[user_id] = user_balances.get(user_id, 0.0) + custom_rates.get(service, 0.10)
        await query.message.reply_text("✅ নম্বরটি ক্যান্সেল করে ব্যালেন্স রিফান্ড করা হয়েছে।")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        user_balances[target_id] = user_balances.get(target_id, 0.0) + amount
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Approved & Balance Added!**")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 **আপনার `${amount}` USDT ডিপোজিট সফলভাবে যুক্ত করা হয়েছে!**")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Deposit Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ আপনার ডিপোজিট রিকোয়েস্টটি বাতিল করা হয়েছে।")


# Deposit Flow (Conversation)
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💛 Binance Pay", callback_data="pay_binance")]
    ])
    await update.message.reply_text("💳 **পেমেন্ট মেথড সিলেক্ট করুন:**", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def deposit_binance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📥 **আপনি কত USDT পাঠাতে চান তা লিখে জানান (যেমন: `5` বা `10`):**")
    return WAITING_AMOUNT

async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        context.user_data["dep_amount"] = amount
        
        msg = (
            f"💰 **অ্যামাউন্ট:** `{amount}` USDT\n\n"
            f"👇 **নিচের Binance Pay ID-তে ডলার পাঠান:**\n"
            f"🆔 Binance ID: `{BINANCE_ID}`\n\n"
            f"ডলার পাঠানোর পর আপনার **Order ID / TxID** লিখে মেসেজ দিন:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return WAITING_TXID
    except ValueError:
        await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন (যেমন: `5`)।")
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    await update.message.reply_text("📸 **এখন আপনার পেমেন্টের স্ক্রিনশট (Photo) পাঠান:**")
    return WAITING_SCREENSHOT

async def deposit_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1] # Highest resolution photo
    amount = context.user_data.get("dep_amount")
    txid = context.user_data.get("dep_txid")

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep_{user.id}_{amount}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep_{user.id}")
        ]
    ])

    caption = (
        f"📥 **নতুন ডিপোজিট রিকোয়েস্ট!**\n\n"
        f"👤 **User:** {user.full_name} (`{user.id}`)\n"
        f"💰 **Amount:** `${amount}` USDT\n"
        f"🧾 **TxID:** `{txid}`"
    )

    # Send to Admin
    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="Markdown", reply_markup=admin_kb)
    await update.message.reply_text("✅ **আপনার ডিপোজিট রিকোয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে!** যাচাই করে দ্রুত ব্যালেন্স যুক্ত করা হবে।")
    return ConversationHandler.END

async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ ডিপোজিট প্রক্রিয়া বাতিল করা হয়েছে।")
    return ConversationHandler.END


# Helper functions
async def manual_check_otp(update, id_num):
    res = fetch_otp_code(id_num)
    if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
        await update.message.reply_text(f"🔑 **আপনার OTP কোড:** `{res['smsCode']}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("⏳ এখনো OTP আসেনি, একটু পর আবার চেষ্টা করুন।")

async def manual_cancel_number(update, user_id, id_num):
    set_number_status(id_num, "bad")
    active_orders.pop(user_id, None)
    service = user_selected_service.get(user_id, "wa")
    user_balances[user_id] = user_balances.get(user_id, 0.0) + custom_rates.get(service, 0.10)
    await update.message.reply_text("✅ নম্বরটি ক্যান্সেল করে ব্যালেন্স রিফান্ড করা হয়েছে।")

async def auto_check_otp(context: ContextTypes.DEFAULT_TYPE, user_id: int, id_num: str, phone_num: str):
    for _ in range(30):
        await asyncio.sleep(6)
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔔 **নতুন OTP এসেছে!**\n\n📱 **নম্বর:** `{phone_num}`\n🔑 **OTP Code:** `{otp}`",
                    parse_mode="Markdown"
                )
                set_number_status(id_num, "end")
            except Exception:
                pass
            break


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    # Deposit Conversation Handler
    dep_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💵 Deposit$"), deposit_start)],
        states={
            WAITING_AMOUNT: [
                CallbackQueryHandler(deposit_binance_selected, pattern="^pay_binance$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount_received)
            ],
            WAITING_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_txid_received)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, deposit_screenshot_received)]
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(dep_handler)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("VAK-SMS Full Featured Bot Running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
