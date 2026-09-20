import logging
import os
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from telegram import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # Apnar Telegram ID
BINANCE_ID = os.getenv("BINANCE_ID", "123456789 (Binance Pay ID)")
ADMIN_BKASH = "01858582881"

# Flask Web Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "VAK-SMS Telegram Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# In-Memory Database
user_selected_country = {}
user_selected_service = {}
active_orders = {}
banned_users = set()
user_balances = {}       # {user_id: balance_amount}
user_otp_counts = {}     # {user_id: count}
user_names = {}          # {user_id: name}
user_subscriptions = {}  # {user_id: expiry_datetime}
custom_rates = {"wa": 0.10, "tg": 0.15, "go": 0.10, "im": 0.10}

# Conversation States
WAITING_AMOUNT, WAITING_TXID, WAITING_SCREENSHOT = range(3)
SUB_AMOUNT, SUB_TXID, SUB_SCREENSHOT = range(3, 6)
ADMIN_BAN, ADMIN_UNBAN, ADMIN_ADD_BAL_USER, ADMIN_ADD_BAL_AMT, ADMIN_RATE_SET = range(6, 11)

# Helper Function: Check Subscription Status
def is_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    if user_id in user_subscriptions:
        expiry = user_subscriptions[user_id]
        if datetime.now() < expiry:
            return True
    return False

# Keyboards
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💳 Account Balance"), KeyboardButton("🛒 Buy Number")],
        [KeyboardButton("🌐 Set Country"), KeyboardButton("📱 Set Service")],
        [KeyboardButton("👤 Profile"), KeyboardButton("💵 Deposit")]
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
    user = update.effective_user
    user_id = user.id

    if user_id in banned_users:
        await update.message.reply_text("❌ Apnar account-ti banned kora hoyeche.", reply_markup=ReplyKeyboardRemove())
        return

    # Init User Data
    user_names[user_id] = user.full_name
    if user_id not in user_balances:
        user_balances[user_id] = 0.0
    if user_id not in user_otp_counts:
        user_otp_counts[user_id] = 0
    if user_id not in user_selected_country:
        user_selected_country[user_id] = "hk"
    if user_id not in user_selected_service:
        user_selected_service[user_id] = "wa"

    # Subscription Check (If not subscribed, remove all bottom features)
    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Buy Subscription (30 Tk / 3 Days)", callback_data="buy_sub_start")]
        ])
        msg = (
            f"👋 **Hello {user.full_name}!**\n\n"
            f"❌ Apnar kache kono active subscription nei!\n"
            f"Bot babohar korte apnake subscription kinte hobe.\n\n"
            f"📌 **Price:** `30 Tk`\n"
            f"⏳ **Validity:** `3 Days`\n\n"
            f"Nicher button-e click kore subscription kinun:"
        )
        # ReplyKeyboardRemove() added so bottom keyboard disappears completely
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 **Buy Subscription:**", reply_markup=sub_kb)
        return

    exp_str = user_subscriptions[user_id].strftime("%Y-%m-%d %H:%M") if user_id != ADMIN_ID else "Unlimited (Admin)"

    welcome_msg = (
        f"👋 **VAK-SMS Bot-e Swagotom!**\n\n"
        f"⚙️ **Bortoman Setup:**\n"
        f"• Country: `{user_selected_country[user_id].upper()}`\n"
        f"• Service: `{user_selected_service[user_id].upper()}`\n"
        f"• Bot Balance: `${user_balances[user_id]:.2f} USDT`\n"
        f"• Subscription Valid Till: `{exp_str}`\n\n"
        f"Nicher menu theke option beche nin:"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    if user_id in banned_users:
        await update.message.reply_text("❌ Apnar account-ti banned kora hoyeche.", reply_markup=ReplyKeyboardRemove())
        return

    # Check Subscription
    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Buy Subscription (30 Tk / 3 Days)", callback_data="buy_sub_start")]
        ])
        await update.message.reply_text("❌ Apnar subscription expired! Doya kore subscription kinun.", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 **Buy Subscription:**", reply_markup=sub_kb)
        return

    text = update.message.text.strip()

    # 1. Balance
    if text == "💳 Account Balance":
        bot_bal = user_balances.get(user_id, 0.0)
        msg = f"💰 **Apnar Bot Balance:** `${bot_bal:.2f} USDT`"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n🏦 **VAK-SMS Site Balance:** `${site_bal:.4f} USD`"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # 2. Profile
    if text == "👤 Profile":
        bot_bal = user_balances.get(user_id, 0.0)
        otp_cnt = user_otp_counts.get(user_id, 0)
        exp_str = user_subscriptions[user_id].strftime("%Y-%m-%d %H:%M") if user_id != ADMIN_ID else "Unlimited (Admin)"
        profile_msg = (
            f"👤 **Apnar Profile Info:**\n\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"📛 **Name:** {user.full_name}\n"
            f"💵 **Balance:** `${bot_bal:.2f} USDT`\n"
            f"📩 **Total OTP Received:** `{otp_cnt}`\n"
            f"📅 **Subscription Valid:** `{exp_str}`"
        )
        await update.message.reply_text(profile_msg, parse_mode="Markdown")
        return

    # 3. Set Country
    if text == "🌐 Set Country":
        country_kb = [
            [KeyboardButton("Country: HK (Hong Kong)"), KeyboardButton("Country: US (USA)")],
            [KeyboardButton("Country: RU (Russia)"), KeyboardButton("Country: IN (India)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text("🌐 **Desh nirbachon korun:**", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if text.startswith("Country:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_country[user_id] = code
        await update.message.reply_text(f"✅ Country set hoyeche: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    # 4. Set Service
    if text == "📱 Set Service":
        service_kb = [
            [KeyboardButton("Service: WA (WhatsApp)"), KeyboardButton("Service: TG (Telegram)")],
            [KeyboardButton("Service: GO (Google/Gmail)"), KeyboardButton("Service: IM (Imo)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text("📱 **Service nirbachon korun:**", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if text.startswith("Service:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_service[user_id] = code
        await update.message.reply_text(f"✅ Service set hoyeche: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🔙 Main Menu":
        await start(update, context)
        return

    # 5. Buy Number (Holding Balance System)
    if text == "🛒 Buy Number":
        country = user_selected_country.get(user_id, "hk")
        service = user_selected_service.get(user_id, "wa")
        bot_rate = custom_rates.get(service, 0.10)
        user_bal = user_balances.get(user_id, 0.0)

        if user_bal < bot_rate:
            await update.message.reply_text(
                f"❌ Porjapto balance nei! Proyojon: `${bot_rate:.2f}` USDT, Apnar ache: `${user_bal:.2f}` USDT.\nDoya kore Deposit korunk."
            )
            return

        status_msg = await update.message.reply_text(f"⏳ `{country.upper()}` desher jonno `{service.upper()}` number kena hocche...")

        res = buy_vak_number(service, country)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            phone_num = res["tel"]
            id_num = str(res["idNum"])

            inline_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Check Active OTP", callback_data=f"check_otp_{id_num}")],
                [InlineKeyboardButton("❌ Cancel Number", callback_data=f"cancel_num_{id_num}")]
            ])

            sent_msg = await update.message.reply_text(
                f"✅ **Number Kena Shofol Hoyeche!**\n\n"
                f"📱 **Number:** `{phone_num}`\n"
                f"🆔 **ID Num:** `{id_num}`\n"
                f"🌍 **Country:** `{country.upper()}`\n"
                f"💬 **Service:** `{service.upper()}`\n"
                f"💵 **Rate:** `${bot_rate:.2f}` USDT *(OTP ashlei balance katbe)*\n\n"
                f"⏳ *OTP pabar jonno apekkha korun...*",
                parse_mode="Markdown",
                reply_markup=inline_kb
            )

            active_orders[id_num] = {
                "user_id": user_id,
                "service": service,
                "cost": bot_rate,
                "phone": phone_num,
                "msg_id": sent_msg.message_id
            }

            try:
                await status_msg.delete()
            except Exception:
                pass

            asyncio.create_task(auto_check_otp(context, user_id, id_num, str(phone_num), sent_msg.message_id))
        else:
            err_msg = res.get("error", "Stock Out") if isinstance(res, dict) else "Error"
            await update.message.reply_text(f"❌ **Number kena shombhov hoyni:** `{err_msg}`")
        return

    # 6. Admin Panel Command
    if text == "⚙️ Admin Panel" and user_id == ADMIN_ID:
        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users")],
            [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
            [InlineKeyboardButton("💵 Set WA Rate", callback_data="admin_rate_start"), InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start")]
        ])
        await update.message.reply_text("🛠 **Admin Control Panel:**", reply_markup=admin_kb)
        return


# Inline Callbacks Processing
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("check_otp_"):
        id_num = data.split("_")[2]
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            await process_otp_success(context, id_num, otp)
        else:
            await query.message.reply_text("⏳ Ekhono OTP asheni, ektu por abar chesta korun.")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        if id_num in active_orders:
            set_number_status(id_num, "bad")
            active_orders.pop(id_num, None)
            await query.edit_message_text(
                f"{query.message.text}\n\n❌ **Number-ti cancel kora hoyeche (Kono balance katini).**",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("❌ Ei order-ti ar active nei ba already OTP ashe geche.")

    elif data == "admin_view_users" and user_id == ADMIN_ID:
        if not user_names:
            await query.message.reply_text("📋 Kono registered user nei.")
            return
        
        msg = "👥 **Registered Users & Status:**\n\n"
        for uid, name in user_names.items():
            bal = user_balances.get(uid, 0.0)
            sub = "Active" if is_subscribed(uid) else "Expired"
            status = "🚫 (Banned)" if uid in banned_users else f"✅ ({sub})"
            msg += f"• **{name}** (`{uid}`): `${bal:.2f}` USDT | Sub: {status}\n"
        
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        user_balances[target_id] = user_balances.get(target_id, 0.0) + amount
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Approved & Balance Added!**")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 **Apnar `${amount}` USDT deposit shofolbhabe jukto kora hoyeche!**")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Deposit Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        user_subscriptions[target_id] = datetime.now() + timedelta(days=3)
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Subscription Approved (3 Days Active)!**")
        
        # Unhide features by sending main keyboard on approval
        await context.bot.send_message(
            chat_id=target_id,
            text="🎉 **Apnar Subscription Approved hoyeche!** 3 Diner jonno bot-er sob features active kora hoyeche.",
            reply_markup=get_main_keyboard(target_id)
        )

    elif data.startswith("reject_sub_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Subscription Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar subscription request-ti batil kora hoyeche.")


async def process_otp_success(context, id_num: str, otp: str):
    if id_num not in active_orders:
        return

    order_info = active_orders.pop(id_num)
    uid = order_info["user_id"]
    cost = order_info["cost"]
    phone = order_info["phone"]
    msg_id = order_info["msg_id"]

    user_balances[uid] = max(0.0, user_balances.get(uid, 0.0) - cost)
    user_otp_counts[uid] = user_otp_counts.get(uid, 0) + 1
    set_number_status(id_num, "end")

    success_text = (
        f"✅ **OTP Received Successfully!**\n\n"
        f"📱 **Number:** `{phone}`\n"
        f"🔑 **OTP Code:** `{otp}`\n\n"
        f"💵 **Balance Deducted:** `${cost:.2f}` USDT\n"
        f"💰 **Remaining Balance:** `${user_balances[uid]:.2f}` USDT"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=uid,
            message_id=msg_id,
            text=success_text,
            parse_mode="Markdown"
        )
    except Exception:
        await context.bot.send_message(chat_id=uid, text=success_text, parse_mode="Markdown")


async def auto_check_otp(context: ContextTypes.DEFAULT_TYPE, user_id: int, id_num: str, phone_num: str, msg_id: int):
    for _ in range(35):
        await asyncio.sleep(6)
        if id_num not in active_orders:
            break

        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            await process_otp_success(context, id_num, otp)
            break


# Subscription Conversation Flow
async def sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bkash_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 Bkash", callback_data="pay_bkash_sub")]
    ])
    await query.message.reply_text("💳 **Payment Method Select Korun:**", reply_markup=bkash_kb)
    return SUB_AMOUNT

async def sub_bkash_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📥 **Subscription Amount (30 Tk) Likhun:**")
    return SUB_AMOUNT

async def sub_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text != "30":
        await update.message.reply_text("❌ Subscription fee shudhu **30** Tk. Doya kore `30` likhun.")
        return SUB_AMOUNT

    msg = (
        f"💰 **Amount:** `30` Tk\n"
        f"⏳ **Validity:** `3 Days`\n\n"
        f"👇 **Nicher Bkash Personal Number-e Send Money Korun:**\n"
        f"📱 Bkash Number: `{ADMIN_BKASH}`\n\n"
        f"Taka pathanor por apnar **TrxID**-ti likhe message din:"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return SUB_TXID

async def sub_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["sub_txid"] = txid
    await update.message.reply_text("📸 **Ekhon Bkash Payment-er Screenshot (Photo) Pathan:**")
    return SUB_SCREENSHOT

async def sub_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    txid = context.user_data.get("sub_txid")

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{user.id}")
        ]
    ])

    caption = (
        f"🔔 **Notun Subscription Request!**\n\n"
        f"👤 **User:** {user.full_name} (`{user.id}`)\n"
        f"💰 **Amount:** `30 Tk`\n"
        f"🧾 **TrxID:** `{txid}`"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="Markdown", reply_markup=admin_kb)
    await update.message.reply_text("✅ **Apnar subscription request admin-er kache pathano hoyeche!** Admin approve korlei bot active hoye jaabe.")
    return ConversationHandler.END


# Deposit Conversation Flow
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💛 Binance Pay", callback_data="pay_binance")]
    ])
    await update.message.reply_text("💳 **Payment Method select korunk:**", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def deposit_binance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📥 **Apni koto USDT pathaben ta likhe janan (jemon: `5` ba `10`):**")
    return WAITING_AMOUNT

async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        context.user_data["dep_amount"] = amount
        
        msg = (
            f"💰 **Amount:** `{amount}` USDT\n\n"
            f"👇 **Nicher Binance Pay ID-te dollar pathan:**\n"
            f"🆔 Binance ID: `{BINANCE_ID}`\n\n"
            f"Dollar pathanor por apnar **Order ID / TxID** likhe message din:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return WAITING_TXID
    except ValueError:
        await update.message.reply_text("❌ Sothik shongkha likhun (jemon: `5`).")
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    await update.message.reply_text("📸 **Ekhon apnar payment-er screenshot (Photo) pathan:**")
    return WAITING_SCREENSHOT

async def deposit_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    amount = context.user_data.get("dep_amount")
    txid = context.user_data.get("dep_txid")

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep_{user.id}_{amount}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep_{user.id}")
        ]
    ])

    caption = (
        f"📥 **Notun Deposit Request!**\n\n"
        f"👤 **User:** {user.full_name} (`{user.id}`)\n"
        f"💰 **Amount:** `${amount}` USDT\n"
        f"🧾 **TxID:** `{txid}`"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="Markdown", reply_markup=admin_kb)
    await update.message.reply_text("✅ **Apnar deposit request admin-er kache pathano hoyeche!** Jaachai kore druto balance jukto kora hobe.")
    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Process batil kora hoyeche.")
    return ConversationHandler.END


# Admin Actions Conversation Handlers
async def admin_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🚫 **Banned korte chawa User ID-ti likhe pathan:**")
    return ADMIN_BAN

async def admin_ban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        banned_users.add(uid)
        await update.message.reply_text(f"✅ User `{uid}`-ke banned kora hoyeche.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
    return ConversationHandler.END

async def admin_unban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✅ **Unban korte chawa User ID-ti likhe pathan:**")
    return ADMIN_UNBAN

async def admin_unban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        banned_users.discard(uid)
        await update.message.reply_text(f"✅ User `{uid}`-ke unban kora hoyeche.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
    return ConversationHandler.END

async def admin_add_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("➕ **Balance add korte chawa User ID-ti pathan:**")
    return ADMIN_ADD_BAL_USER

async def admin_add_bal_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        context.user_data["target_add_uid"] = uid
        await update.message.reply_text(f"💰 **User `{uid}`-er jonno koto USDT balance add korben ta likhun:**", parse_mode="Markdown")
        return ADMIN_ADD_BAL_AMT
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
        return ConversationHandler.END

async def admin_add_bal_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.strip())
        uid = context.user_data.get("target_add_uid")
        user_balances[uid] = user_balances.get(uid, 0.0) + amt
        await update.message.reply_text(f"✅ Successfully added `${amt}` USDT to User `{uid}`. Notun Balance: `${user_balances[uid]:.2f}` USDT", parse_mode="Markdown")
        await context.bot.send_message(chat_id=uid, text=f"🎉 **Admin apnar account-e `${amt}` USDT balance add koreche!**")
    except ValueError:
        await update.message.reply_text("❌ Invalid Amount.")
    return ConversationHandler.END

async def admin_rate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 **WhatsApp (WA)-er notun Bot Rate USDT-te likhun (jemon: `0.10` ba `0.15`):**")
    return ADMIN_RATE_SET

async def admin_rate_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        custom_rates["wa"] = rate
        await update.message.reply_text(f"✅ WhatsApp Bot Rate update kora hoyeche: `${rate:.2f}` USDT")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate.")
    return ConversationHandler.END


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    # Subscription Flow Handler
    sub_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(sub_start, pattern="^buy_sub_start$")],
        states={
            SUB_AMOUNT: [
                CallbackQueryHandler(sub_bkash_selected, pattern="^pay_bkash_sub$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sub_amount_received)
            ],
            SUB_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_txid_received)],
            SUB_SCREENSHOT: [MessageHandler(filters.PHOTO, sub_screenshot_received)]
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)]
    )

    # Deposit Flow Handler
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
        fallbacks=[CommandHandler("cancel", cancel_flow)]
    )

    # Admin Conversation Handler
    admin_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_ban_start, pattern="^admin_ban_start$"),
            CallbackQueryHandler(admin_unban_start, pattern="^admin_unban_start$"),
            CallbackQueryHandler(admin_add_bal_start, pattern="^admin_add_bal_start$"),
            CallbackQueryHandler(admin_rate_start, pattern="^admin_rate_start$"),
        ],
        states={
            ADMIN_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_process)],
            ADMIN_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_process)],
            ADMIN_ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_user)],
            ADMIN_ADD_BAL_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_amt)],
            ADMIN_RATE_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_process)],
        },
        fallbacks=[CommandHandler("cancel", cancel_flow)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(sub_handler)
    app.add_handler(dep_handler)
    app.add_handler(admin_handler)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("VAK-SMS Full Bot Running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
