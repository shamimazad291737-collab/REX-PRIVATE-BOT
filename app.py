import logging
import os
import threading
import asyncio
import re
from datetime import datetime
from flask import Flask
from pymongo import MongoClient
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
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY", "087d6bfb54884a7bbfd963a232add065")

ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "123456789")
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_RAW.split(",") if x.strip().isdigit()]

OTP_GROUP_ID = os.getenv("OTP_GROUP_ID")
BINANCE_ID = os.getenv("BINANCE_ID", "1102671249")
MONGODB_URI = os.getenv("MONGODB_URI")

# MongoDB Setup
if not MONGODB_URI:
    logging.error("❌ MONGODB_URI Environment Variable missing!")
client = MongoClient(MONGODB_URI)
db = client["chile_wa_bot_db"]

users_col = db["users"]
settings_col = db["settings"]
deposits_col = db["pending_deposits"]
otp_stats_col = db["otp_stats"]

# Flask Web Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "Chile WA Telegram Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# In-Memory Active Orders
active_orders = {}

# Conversation States
WAITING_AMOUNT, WAITING_TXID, WAITING_SCREENSHOT = range(3)
(
    ADMIN_BAN,
    ADMIN_UNBAN,
    ADMIN_ADD_BAL_USER,
    ADMIN_ADD_BAL_AMT,
    ADMIN_ZERO_BAL_USER,
    ADMIN_RATE_WA_CL_SET,
    ADMIN_BROADCAST,
) = range(3, 10)

# Helper Functions
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def mask_number(phone_str: str) -> str:
    clean_num = re.sub(r"[^\d+]", "", str(phone_str))
    if len(clean_num) <= 6:
        return clean_num
    prefix = clean_num[:4] if clean_num.startswith("+") else clean_num[:3]
    suffix = clean_num[-4:]
    masked_part = "*" * (len(clean_num) - len(suffix) - len(prefix))
    return f"{prefix}{masked_part}{suffix}"

def get_user(user_id: int):
    return users_col.find_one({"user_id": user_id})

def get_or_create_user(user_id: int, full_name: str = "User"):
    user = users_col.find_one({"user_id": user_id})
    if not user:
        user_data = {
            "user_id": user_id,
            "full_name": full_name,
            "balance": 0.0,
            "otp_count": 0,
            "selected_country": "cl",
            "selected_service": "wa",
            "is_banned": False,
        }
        users_col.insert_one(user_data)
        return user_data
    else:
        users_col.update_one({"user_id": user_id}, {"$set": {"full_name": full_name}})
        return user

def get_rate():
    doc = settings_col.find_one({"type": "rates"})
    if doc and "rates" in doc and "wa_cl" in doc["rates"]:
        return float(doc["rates"]["wa_cl"])
    return 0.10

def set_rate(rate: float):
    settings_col.update_one(
        {"type": "rates"},
        {"$set": {"rates.wa_cl": rate}},
        upsert=True
    )

def is_bot_active() -> bool:
    doc = settings_col.find_one({"type": "bot_status"})
    if doc:
        return doc.get("is_active", True)
    return True

def set_bot_active(status: bool):
    settings_col.update_one(
        {"type": "bot_status"},
        {"$set": {"is_active": status}},
        upsert=True
    )

def record_otp_purchase(country: str):
    today = datetime.now().strftime("%Y-%m-%d")
    otp_stats_col.update_one(
        {"date": today, "country": country.upper()},
        {"$inc": {"count": 1}},
        upsert=True
    )

# Keyboards
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴"), KeyboardButton("🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁")],
        [KeyboardButton("👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴"), KeyboardButton("💵 𝙳𝙸𝙿𝙾𝚂𝙸𝚃")]
    ]
    if is_admin(user_id):
        keyboard.append([KeyboardButton("⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# VAK-SMS API Functions
def set_number_status(id_num: str, status: str):
    url = f"https://vak-sms.com/api/setStatus/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}&status={status}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}

def get_vak_balance():
    url = f"https://vak-sms.com/api/getBalance/?apiKey={VAK_SMS_API_KEY}"
    try:
        res = requests.get(url).json()
        return float(res.get("balance", 0.0))
    except Exception:
        return 0.0

def buy_vak_number(max_price: float = 0.087):
    current_panel_bal = get_vak_balance()
    if current_panel_bal < max_price:
        return {"error": "Stock Out!"}

    url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service=wa&country=cl&maxPrice={max_price}"
    try:
        res = requests.get(url).json()
        
        if isinstance(res, dict) and res.get("error") in ["noNumber", "noBalance"]:
            return {"error": "Stock Out!"}
            
        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            assigned_price = res.get("price")
            if assigned_price is not None:
                try:
                    price_val = float(assigned_price)
                    if price_val > max_price:
                        id_num = str(res["idNum"])
                        set_number_status(id_num, "bad")
                        return {"error": "Stock Out!"}
                except ValueError:
                    pass

        return res
    except Exception:
        return {"error": "Stock Out!"}

def fetch_otp_code(id_num: str):
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text("🚫 BAN BY ADMIN. CONTACT ADMIN.", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and not is_admin(user_id):
        await update.message.reply_text("🚧 BOT UNDER MAINTENANCE BY ADMIN. PLEASE TRY LATER.")
        return

    welcome_msg = (
        f"👋 WELCOME CHILE WHATSAPP BOT!\n\n"
        f"⚙️ TGS WA NUMBER"
    )
    await update.message.reply_text(welcome_msg, reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    u_data = get_or_create_user(user_id, update.effective_user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text("🚫 BAN BY ADMIN. CONTACT ADMIN.")
        return

    if not is_bot_active() and not is_admin(user_id):
        await update.message.reply_text("🚧 BOT UNDER MAINTENANCE BY ADMIN. PLEASE TRY LATER.")
        return

    if text == "💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"💰 MY BALANCE: {bot_bal:.4f} USDT"
        
        if is_admin(user_id):
            site_bal = get_vak_balance()
            available_numbers = int(site_bal // 0.079)
            msg += f"\n📊 AVAILABLE NUMBERS: {available_numbers} Pcs"
            
        await update.message.reply_text(msg)
        return

    elif text == "🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁":
        if user_id in active_orders:
            await update.message.reply_text("🚫 YOU ALREADY HAVE AN ACTIVE NUMBER. CANCEL OR FINISH IT FIRST.")
            return

        rate = get_rate()
        user_bal = u_data.get("balance", 0.0)

        if user_bal < rate:
            await update.message.reply_text(f"🚫 INSUFFICIENT BALANCE!\n⚡ NEED: ${rate:.2f} USDT\n💰 YOUR BALANCE: ${user_bal:.2f} USDT")
            return

        await update.message.reply_text("⏳ WAIT FOR NUMBER...")

        res = buy_vak_number()
        if "error" in res or "tel" not in res or "idNum" not in res:
            await update.message.reply_text("🚫 Stock Out!")
            return

        phone_num = str(res["tel"])
        id_num = str(res["idNum"])

        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 CANCEL NUMBER", callback_data="cancel_number")]
        ])

        buying_msg = (
            f"✅ NUMBER PURCHASED!\n\n"
            f"📱 NUMBER: {phone_num}\n"
            f"💰 PRICE: ${rate:.2f} USDT\n\n"
            f"⏳ WAITING FOR OTP (AUTO POLLING)..."
        )
        sent_msg = await update.message.reply_text(buying_msg, reply_markup=inline_kb)

        active_orders[user_id] = {
            "idNum": id_num,
            "phone": phone_num,
            "cost": rate,
            "status": "WAITING_OTP",
            "message_id": sent_msg.message_id,
            "cancel_task": None,
            "poll_task": None
        }

        async def auto_cancel():
            await asyncio.sleep(300)
            if user_id in active_orders and active_orders[user_id]["status"] == "WAITING_OTP":
                set_number_status(id_num, "bad")
                msg_id = active_orders[user_id]["message_id"]
                del active_orders[user_id]
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                except Exception:
                    pass

        async def poll_otp():
            while user_id in active_orders and active_orders[user_id]["status"] == "WAITING_OTP":
                await asyncio.sleep(1)
                sms_res = fetch_otp_code(id_num)
                
                if isinstance(sms_res, dict) and sms_res.get("smsCode"):
                    otp_code = str(sms_res["smsCode"])
                    order_info = active_orders[user_id]
                    order_info["status"] = "COMPLETED"
                    order_msg_id = order_info["message_id"]

                    if order_info["cancel_task"]:
                        order_info["cancel_task"].cancel()

                    users_col.update_one(
                        {"user_id": user_id},
                        {
                            "$inc": {
                                "balance": -order_info["cost"],
                                "otp_count": 1
                            }
                        }
                    )

                    set_number_status(id_num, "end")
                    record_otp_purchase("CHILE")
                    del active_orders[user_id]

                    otp_msg = (
                        f"✅ OTP RECEIVED SUCCESSFULLY!\n\n"
                        f"📱 NUMBER: {phone_num}\n"
                        f"💬 OTP CODE: {otp_code}\n\n"
                        f"💰 DEDUCTED: ${rate:.2f} USDT"
                    )
                    
                    full_sms = sms_res.get("sms", "")
                    inline_kb_copy = None
                    if full_sms:
                        inline_kb_copy = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📋 COPY SMS", callback_data=f"copy_sms:{user_id}")]
                        ])
                        context.user_data[f"full_sms_{user_id}"] = full_sms

                    try:
                        await context.bot.edit_message_text(
                            chat_id=user_id,
                            message_id=order_msg_id,
                            text=otp_msg,
                            reply_markup=inline_kb_copy
                        )
                    except Exception as e:
                        logging.error(f"Failed to edit message: {e}")

                    if OTP_GROUP_ID:
                        try:
                            m_num = mask_number(phone_num)
                            group_text = (
                                f"🎉 NEW SUCCESSFUL OTP!\n\n"
                                f"👤 USER ID: {user_id}\n"
                                f"📱 NUMBER: {m_num}\n"
                                f"💬 OTP CODE: {otp_code}"
                            )
                            await context.bot.send_message(chat_id=OTP_GROUP_ID, text=group_text)
                        except Exception as e:
                            logging.error(f"Failed to send to group: {e}")
                    break

        cancel_task = asyncio.create_task(auto_cancel())
        poll_task = asyncio.create_task(poll_otp())
        
        active_orders[user_id]["cancel_task"] = cancel_task
        active_orders[user_id]["poll_task"] = poll_task

    elif text == "👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴":
        p_msg = (
            f"👤 USER PROFILE\n\n"
            f"🆔 USER ID: {user_id}\n"
            f"📛 NAME: {u_data.get('full_name', 'User')}\n"
            f"💰 BALANCE: ${u_data.get('balance', 0.0):.4f} USDT\n"
            f"📊 TOTAL OTP BOUGHT: {u_data.get('otp_count', 0)}"
        )
        await update.message.reply_text(p_msg)

    elif text == "💵 𝙳𝙸𝙿𝙾𝚂𝙸𝚃":
        dep_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟡 BINANCE PAY", callback_data="pay_binance")]
        ])
        await update.message.reply_text("💳 SELECT DEPOSIT METHOD:", reply_markup=dep_kb)

    elif text == "⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻" and is_admin(user_id):
        curr_status = is_bot_active()
        status_text = "🟢 ACTIVE" if curr_status else "🔴 OFF (MAINTENANCE)"

        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚫 BAN USER", callback_data="admin_ban"), InlineKeyboardButton("✅ UNBAN USER", callback_data="admin_unban")],
            [InlineKeyboardButton("➕ ADD BALANCE", callback_data="admin_add_bal"), InlineKeyboardButton("🧹 ZERO BALANCE", callback_data="admin_zero_bal")],
            [InlineKeyboardButton("📊 VIEW USERS", callback_data="admin_view_users"), InlineKeyboardButton("⚙️ SET RATE", callback_data="admin_set_rate")],
            [InlineKeyboardButton("📈 DAILY STATS", callback_data="admin_daily_stats"), InlineKeyboardButton(f"🤖 BOT STATUS: {status_text}", callback_data="admin_toggle_bot")],
            [InlineKeyboardButton("📢 BROADCAST", callback_data="admin_broadcast")]
        ])
        await update.message.reply_text("⚙ ADMIN CONTROL PANEL", reply_markup=admin_kb)

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "cancel_number":
        if user_id in active_orders:
            order = active_orders[user_id]
            id_num = order["idNum"]
            msg_id = order["message_id"]

            if order["cancel_task"]:
                order["cancel_task"].cancel()
            if order["poll_task"]:
                order["poll_task"].cancel()

            set_number_status(id_num, "bad")
            del active_orders[user_id]

            try:
                await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
            except Exception as e:
                logging.error(f"Failed to delete message: {e}")
        else:
            await query.answer("🚫 No active number to cancel.", show_alert=True)

    elif data.startswith("copy_sms:"):
        target_uid = int(data.split(":")[1])
        sms_text = context.user_data.get(f"full_sms_{target_uid}", "No SMS Text Available")
        await query.message.reply_text(f"📋 FULL SMS:\n{sms_text}")

    elif is_admin(user_id):
        if data == "admin_toggle_bot":
            curr = is_bot_active()
            new_status = not curr
            set_bot_active(new_status)
            status_text = "🟢 ACTIVE" if new_status else "🔴 OFF (MAINTENANCE)"
            
            admin_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🚫 BAN USER", callback_data="admin_ban"), InlineKeyboardButton("✅ UNBAN USER", callback_data="admin_unban")],
                [InlineKeyboardButton("➕ ADD BALANCE", callback_data="admin_add_bal"), InlineKeyboardButton("🧹 ZERO BALANCE", callback_data="admin_zero_bal")],
                [InlineKeyboardButton("📊 VIEW USERS", callback_data="admin_view_users"), InlineKeyboardButton("⚙️ SET RATE", callback_data="admin_set_rate")],
                [InlineKeyboardButton("📈 DAILY STATS", callback_data="admin_daily_stats"), InlineKeyboardButton(f"🤖 BOT STATUS: {status_text}", callback_data="admin_toggle_bot")],
                [InlineKeyboardButton("📢 BROADCAST", callback_data="admin_broadcast")]
            ])
            await query.edit_message_reply_markup(reply_markup=admin_kb)

        elif data == "admin_daily_stats":
            today = datetime.now().strftime("%Y-%m-%d")
            records = list(otp_stats_col.find({"date": today}))
            
            if not records:
                await query.message.reply_text(f"📈 DAILY OTP STATS ({today}):\n\nNo OTPs purchased today.")
                return

            msg = f"📈 DAILY OTP STATS ({today}):\n\n"
            total_today = 0
            for r in records:
                c_name = r.get("country", "UNKNOWN")
                cnt = r.get("count", 0)
                total_today += cnt
                msg += f"🏳️ Country: {c_name} -> {cnt} Pcs\n"
            
            msg += f"\n📊 Total Received Today: {total_today} Pcs"
            await query.message.reply_text(msg)

        elif data == "admin_view_users":
            all_users = list(users_col.find({}))
            total_users = len(all_users)
            
            if total_users == 0:
                await query.message.reply_text("📋 NO USERS FOUND.")
                return

            text_msg = f"📊 TOTAL BOT USERS: {total_users}\n\n"
            for u in all_users:
                text_msg += (
                    f"👤 Name: {u.get('full_name', 'N/A')}\n"
                    f"🆔 ID: {u.get('user_id')}\n"
                    f"💰 Balance: ${u.get('balance', 0.0):.4f} USDT\n"
                    f"📥 OTP Bought: {u.get('otp_count', 0)}\n"
                    f"🚫 Status: {'Banned' if u.get('is_banned') else 'Active'}\n"
                    f"----------------------------\n"
                )

            if len(text_msg) > 4000:
                for i in range(0, len(text_msg), 4000):
                    await query.message.reply_text(text_msg[i:i+4000])
            else:
                await query.message.reply_text(text_msg)

        elif data == "admin_ban":
            await query.message.reply_text("SEND USER ID TO BAN:")
            return ADMIN_BAN

        elif data == "admin_unban":
            await query.message.reply_text("SEND USER ID TO UNBAN:")
            return ADMIN_UNBAN

        elif data == "admin_add_bal":
            await query.message.reply_text("SEND USER ID TO ADD BALANCE:")
            return ADMIN_ADD_BAL_USER

        elif data == "admin_zero_bal":
            await query.message.reply_text("SEND USER ID TO ZERO BALANCE:")
            return ADMIN_ZERO_BAL_USER

        elif data == "admin_set_rate":
            curr_rate = get_rate()
            await query.message.reply_text(f"CURRENT CHILE WA RATE IS: ${curr_rate} USDT\nSEND NEW RATE FOR CHILE WA:")
            return ADMIN_RATE_WA_CL_SET

        elif data == "admin_broadcast":
            await query.message.reply_text("SEND BROADCAST MESSAGE TO ALL USERS:")
            return ADMIN_BROADCAST

# Deposit Conversation Handlers
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
        u_data = get_or_create_user(user_id, query.from_user.full_name)
    else:
        user_id = update.effective_user.id
        u_data = get_or_create_user(user_id, update.effective_user.full_name)

    if u_data.get("is_banned", False):
        if query:
            await query.message.reply_text("🚫 BAN BY ADMIN. CONTACT ADMIN.")
        else:
            await update.message.reply_text("🚫 BAN BY ADMIN. CONTACT ADMIN.")
        return ConversationHandler.END

    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 CANCEL DEPOSIT", callback_data="cancel_deposit")]
    ])

    dep_msg = (
        f"💵 DEPOSIT VIA BINANCE PAY\n\n"
        f"🆔 BINANCE PAY ID: {BINANCE_ID}\n"
        f"⚠️ MINIMUM DEPOSIT: $0.11 USDT\n\n"
        f"✍️ SEND THE AMOUNT (USDT) YOU HAVE SENT:"
    )
    if query:
        await query.message.reply_text(dep_msg, reply_markup=cancel_kb)
    else:
        await update.message.reply_text(dep_msg, reply_markup=cancel_kb)
        
    return WAITING_AMOUNT

async def deposit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 CANCEL DEPOSIT", callback_data="cancel_deposit")]
    ])
    try:
        amount = float(text)
        if amount < 0.11:
            await update.message.reply_text("🚫 MINIMUM DEPOSIT IS $0.11 USDT!\nPlease enter an amount equal to or greater than $0.11:", reply_markup=cancel_kb)
            return WAITING_AMOUNT

        context.user_data["dep_amount"] = amount
        await update.message.reply_text("📥 SEND YOUR BINANCE PAY TXID / ORDER ID:", reply_markup=cancel_kb)
        return WAITING_TXID
    except ValueError:
        await update.message.reply_text("🚫 INVALID AMOUNT. PLEASE ENTER A NUMBER (E.G. 0.50):", reply_markup=cancel_kb)
        return WAITING_AMOUNT

async def deposit_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text
    context.user_data["dep_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 CANCEL DEPOSIT", callback_data="cancel_deposit")]
    ])
    await update.message.reply_text("📸 SEND A SCREENSHOT OF THE PAYMENT:", reply_markup=cancel_kb)
    return WAITING_SCREENSHOT

async def deposit_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    photo = update.message.photo[-1]
    photo_file_id = photo.file_id

    amount = context.user_data.get("dep_amount", 0.0)
    txid = context.user_data.get("dep_txid", "N/A")

    await update.message.reply_text("✅ DEPOSIT REQUEST SUBMITTED!\nAdmin will verify and add your balance soon.")

    dep_id = f"{user_id}_{int(datetime.now().timestamp())}"
    admin_messages = []

    for admin_id in ADMIN_IDS:
        try:
            admin_msg = (
                f"📥 NEW DEPOSIT REQUEST!\n\n"
                f"👤 USER: {user.full_name} ({user_id})\n"
                f"💰 AMOUNT: ${amount:.2f} USDT\n"
                f"🆔 TXID: {txid}"
            )
            approve_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"app_dep:{dep_id}:{user_id}:{amount}"),
                    InlineKeyboardButton("🚫 REJECT", callback_data=f"rej_dep:{dep_id}:{user_id}")
                ]
            ])
            sent_m = await context.bot.send_photo(
                chat_id=admin_id,
                photo=photo_file_id,
                caption=admin_msg,
                reply_markup=approve_kb
            )
            admin_messages.append({"chat_id": admin_id, "message_id": sent_m.message_id})
        except Exception as e:
            logging.error(f"Failed to send deposit to admin {admin_id}: {e}")

    deposits_col.insert_one({
        "dep_id": dep_id,
        "user_id": user_id,
        "amount": amount,
        "status": "PENDING",
        "admin_messages": admin_messages
    })

    return ConversationHandler.END

async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("🚫 DEPOSIT PROCESS CANCELLED!")
    return ConversationHandler.END

# Admin Conversation Handlers
async def admin_ban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
        users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"✅ USER {target_id} HAS BEEN BANNED.")
    except ValueError:
        await update.message.reply_text("🚫 INVALID USER ID.")
    return ConversationHandler.END

async def admin_unban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
        users_col.update_one({"user_id": target_id}, {"$set": {"is_banned": False}})
        await update.message.reply_text(f"✅ USER {target_id} HAS BEEN UNBANNED.")
    except ValueError:
        await update.message.reply_text("🚫 INVALID USER ID.")
    return ConversationHandler.END

async def admin_add_bal_user_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
        context.user_data["target_user_id"] = target_id
        await update.message.reply_text("SEND AMOUNT TO ADD:")
        return ADMIN_ADD_BAL_AMT
    except ValueError:
        await update.message.reply_text("🚫 INVALID USER ID.")
        return ConversationHandler.END

async def admin_add_bal_amt_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text)
        target_id = context.user_data.get("target_user_id")
        users_col.update_one({"user_id": target_id}, {"$inc": {"balance": amt}})
        await update.message.reply_text(f"✅ ADDED ${amt:.2f} USDT TO USER {target_id}.")
        try:
            await context.bot.send_message(target_id, f"🎉 Apna ${amt:.2f} USDT deposit sofolvabe jukto kora hoyeche!")
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("🚫 INVALID AMOUNT.")
    return ConversationHandler.END

async def admin_zero_bal_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
        users_col.update_one({"user_id": target_id}, {"$set": {"balance": 0.0}})
        await update.message.reply_text(f"✅ BALANCE ZEROED FOR USER {target_id}.")
    except ValueError:
        await update.message.reply_text("🚫 INVALID USER ID.")
    return ConversationHandler.END

async def admin_set_rate_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_rate = float(update.message.text)
        set_rate(new_rate)
        await update.message.reply_text(f"✅ CHILE WA RATE UPDATED TO: ${new_rate} USDT")
    except ValueError:
        await update.message.reply_text("🚫 INVALID RATE AMOUNT.")
    return ConversationHandler.END

async def admin_broadcast_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text
    users = users_col.find({})
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u["user_id"], msg_text)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"📢 BROADCAST SENT TO {count} USERS.")
    return ConversationHandler.END

async def admin_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    admin_id = query.from_user.id

    if not is_admin(admin_id):
        return

    parts = data.split(":")
    action = parts[0]
    dep_id = parts[1]
    target_uid = int(parts[2])

    dep_doc = deposits_col.find_one({"dep_id": dep_id})
    if not dep_doc or dep_doc.get("status") != "PENDING":
        await query.message.reply_text("⚠️ THIS DEPOSIT REQUEST WAS ALREADY PROCESSED.")
        return

    admin_messages = dep_doc.get("admin_messages", [])

    if action == "app_dep":
        amt = float(parts[3])
        users_col.update_one({"user_id": target_uid}, {"$inc": {"balance": amt}})
        deposits_col.update_one({"dep_id": dep_id}, {"$set": {"status": "APPROVED"}})

        for amsg in admin_messages:
            try:
                await context.bot.edit_message_caption(
                    chat_id=amsg["chat_id"],
                    message_id=amsg["message_id"],
                    caption=f"{query.message.caption}\n\n✅ APPROVED BY ADMIN"
                )
            except Exception as e:
                logging.error(f"Failed to sync approve status to admin {amsg['chat_id']}: {e}")

        try:
            await context.bot.send_message(target_uid, f"🎉 Apna ${amt:.2f} USDT deposit sofolvabe jukto kora hoyeche!")
        except Exception:
            pass

    elif action == "rej_dep":
        deposits_col.update_one({"dep_id": dep_id}, {"$set": {"status": "REJECTED"}})

        for amsg in admin_messages:
            try:
                await context.bot.edit_message_caption(
                    chat_id=amsg["chat_id"],
                    message_id=amsg["message_id"],
                    caption=f"{query.message.caption}\n\n🚫 REJECTED BY ADMIN"
                )
            except Exception as e:
                logging.error(f"Failed to sync reject status to admin {amsg['chat_id']}: {e}")

        try:
            await context.bot.send_message(target_uid, "🚫 Apna deposit request ti reject kora hoyeche!")
        except Exception:
            pass

async def start_bot():
    threading.Thread(target=run_flask, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    deposit_handler = ConversationHandler(
        entry_points=[
            CommandHandler("deposit", deposit_start),
            CallbackQueryHandler(deposit_start, pattern="^pay_binance$")
        ],
        states={
            WAITING_AMOUNT: [
                CallbackQueryHandler(deposit_cancel, pattern="^cancel_deposit$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount)
            ],
            WAITING_TXID: [
                CallbackQueryHandler(deposit_cancel, pattern="^cancel_deposit$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_txid)
            ],
            WAITING_SCREENSHOT: [
                CallbackQueryHandler(deposit_cancel, pattern="^cancel_deposit$"),
                MessageHandler(filters.PHOTO, deposit_screenshot)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(deposit_cancel, pattern="^cancel_deposit$")
        ]
    )

    admin_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_callback_query, pattern="^admin_")
        ],
        states={
            ADMIN_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_process)],
            ADMIN_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_process)],
            ADMIN_ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_user_process)],
            ADMIN_ADD_BAL_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_amt_process)],
            ADMIN_ZERO_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_zero_bal_process)],
            ADMIN_RATE_WA_CL_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_rate_process)],
            ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_process)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(deposit_handler)
    app.add_handler(admin_handler)
    app.add_handler(CallbackQueryHandler(admin_deposit_callback, pattern="^(app_dep|rej_dep):"))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    logging.info("Starting Telegram Bot...")
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        # Keep bot running
        await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
