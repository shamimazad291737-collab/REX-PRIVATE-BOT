import logging
import os
import threading
import asyncio
import re
from datetime import datetime, timedelta
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
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY", "893d842ab70a4e79b4ad323185a69257")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
OTP_GROUP_ID = os.getenv("OTP_GROUP_ID")
BINANCE_ID = os.getenv("BINANCE_ID", "907194603")
ADMIN_BKASH = "01858582881"
MONGODB_URI = os.getenv("MONGODB_URI")

# MongoDB Setup
if not MONGODB_URI:
    logging.error("❌ MONGODB_URI Environment Variable missing!")
client = MongoClient(MONGODB_URI)
db = client["vaksms_bot_db"]

users_col = db["users"]
settings_col = db["settings"]
otp_logs_col = db["otp_logs"]

# Flask Web Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "Rex Private Telegram Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# In-Memory Active Orders
active_orders = {}

# Conversation States
WAITING_AMOUNT, WAITING_TXID, WAITING_SCREENSHOT = range(3)
SUB_PLAN, SUB_METHOD, SUB_TXID, SUB_SCREENSHOT = range(3, 7)
(
    ADMIN_BAN,
    ADMIN_UNBAN,
    ADMIN_ADD_BAL_USER,
    ADMIN_ADD_BAL_AMT,
    ADMIN_ZERO_BAL_USER,
    ADMIN_RATE_WA_HK_SET,
    ADMIN_RATE_WA_CL_SET,
    ADMIN_RATE_TG_HK_SET,
    ADMIN_RATE_TG_CL_SET,
    ADMIN_BROADCAST,
) = range(7, 17)

# Helper Functions
def get_country_flag(country_code: str) -> str:
    code = country_code.lower()
    if code == "hk":
        return "🇭🇰"
    elif code == "cl":
        return "🇨🇱"
    return "🌐"

def mask_number(phone_str: str) -> str:
    clean_num = re.sub(r"[^\d+]", "", str(phone_str))
    if len(clean_num) <= 6:
        return clean_num
    prefix = clean_num[:4] if clean_num.startswith("+") else clean_num[:3]
    suffix = clean_num[-4:]
    masked_part = "*" * (len(clean_num) - len(prefix) - len(suffix))
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
            "selected_country": "hk",
            "selected_service": "tg",
            "is_banned": False,
            "subscription_expiry": None,
            "sub_days": 0,
            "sub_method": "bkash"
        }
        users_col.insert_one(user_data)
        return user_data
    else:
        users_col.update_one({"user_id": user_id}, {"$set": {"full_name": full_name}})
        return user

def get_rate(service_code: str = "tg", country_code: str = "hk"):
    doc = settings_col.find_one({"type": "rates"})
    key = f"{service_code.lower()}_{country_code.lower()}"
    
    if doc and "rates" in doc and key in doc["rates"]:
        return float(doc["rates"][key])
    
    defaults = {
        "wa_hk": 0.10,
        "wa_cl": 0.10,
        "tg_hk": 0.12,
        "tg_cl": 0.12
    }
    return defaults.get(key, 0.10)

def set_rate(service_code: str, country_code: str, rate: float):
    key = f"{service_code.lower()}_{country_code.lower()}"
    settings_col.update_one(
        {"type": "rates"},
        {"$set": {f"rates.{key}": rate}},
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

def is_subscribed(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    if user and user.get("subscription_expiry"):
        expiry = user["subscription_expiry"]
        if datetime.now() < expiry:
            return True
    return False

# Keyboards
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴"), KeyboardButton("🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁")],
        [KeyboardButton("🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂"), KeyboardButton("📱 𝚂𝙴𝚃 𝚂𝙴𝚁𝚅𝙸𝙲𝙴")],
        [KeyboardButton("👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴"), KeyboardButton("💵 𝙳𝙸𝙿𝙾𝚂𝙸𝚃")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# VAK-SMS API Functions
def set_number_status(id_num: str, status: str):
    url = f"https://vak-sms.com/api/setStatus/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}&status={status}"
    try:
        return requests.get(url).json()
    except Exception as e:
        return {"error": str(e)}

def buy_vak_number(service: str = "tg", country: str = "hk", max_price: float = 0.087):
    url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service}&country={country}&maxPrice={max_price}"
    try:
        res = requests.get(url).json()
        
        if isinstance(res, dict) and res.get("error") == "noNumber":
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
    except Exception as e:
        return {"error": "Stock Out!"}

def get_vak_balance():
    url = f"https://vak-sms.com/api/getBalance/?apiKey={VAK_SMS_API_KEY}"
    try:
        res = requests.get(url).json()
        return res.get("balance", 0.0)
    except Exception:
        return 0.0

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
        await update.message.reply_text("❌ BAN BY ADMIN CONTACT ADMIN.", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 BOT UNDER MAINTAINING BY ADMIN. Please try some time.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        msg = (
            f"👋 Hello {user.full_name}!\n\n"
            f"❌ YOU DONT HAVE SUBSCRIPTION FOR THE BOT!\n"
            f"Bot bebohar korte chaile subscription nite hobe.\n\n"
            f"📌 PRICE & VALIDITY (Rate: 125 Tk/$) :\n"
            f"• 5 Days = 50 Tk (or 0.40 USDT)\n"
            f"• 7 Days = 70 Tk (or 0.56 USDT)\n\n"
            f"Nicher button theke click kore subscription kinun:"
        )
        await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    exp_time = u_data.get("subscription_expiry")
    exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"

    curr_country_code = u_data.get("selected_country", "hk")
    curr_country = curr_country_code.upper()
    country_flag = get_country_flag(curr_country_code)
    curr_service = u_data.get("selected_service", "tg").upper()

    welcome_msg = (
        f"👋 WELCOME REX PRIVATE BOT!\n\n"
        f"⚙️ RECENT SETUP:\n"
        f"• COUNTRIES: {curr_country} {country_flag}\n"
        f"• Service: {curr_service}\n"
        f"• YOUR BALANCE: {u_data.get('balance', 0.0):.4f} USDT\n"
        f"• SUBSCRIPTION VALID TILL: {exp_str}\n\n"
        f"Kaj korte niche dea menu use karen:"
    )
    await update.message.reply_text(welcome_msg, reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text("❌ YOUR ACCOUNT HAS BEEN BANNED BY ADMIN.", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 BOT UNDER MAINTAIN BY ADMIN. Try again after some time.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        await update.message.reply_text("❌ SUBSCRIPTION EXPIRED! BUY NEW SUBSCRIPTION.", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    text = update.message.text.strip()

    if text == "💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"💰 MY BALANCE: {bot_bal:.4f} USDT"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n🏦 PANEL BALANCE : {site_bal:.4f} USD"
        await update.message.reply_text(msg)
        return

    if text == "👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴":
        bot_bal = u_data.get("balance", 0.0)
        otp_cnt = u_data.get("otp_count", 0)
        exp_time = u_data.get("subscription_expiry")
        exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"
        profile_msg = (
            f"👤 Apnar Profile Info:\n\n"
            f"🆔 User ID: {user_id}\n"
            f"📛 Name: {user.full_name}\n"
            f"💵 Balance: {bot_bal:.4f} USDT\n"
            f"📩 Total OTP Received: {otp_cnt}\n"
            f"📅 Subscription Valid: {exp_str}"
        )
        await update.message.reply_text(profile_msg)
        return

    # COUNTRY SELECTION
    if text in ["🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝚈", "🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂"]:
        country_kb = [
            [KeyboardButton("COUNTRY: HK 🇭🇰 (HONG KONG)"), KeyboardButton("COUNTRY: CHILE 🇨🇱 (CL)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text("🌐 SELECT YOUR COUNTRY:", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if "HK" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "hk"}})
        await update.message.reply_text("✅ Country set: HONG KONG (HK) 🇭🇰", reply_markup=get_main_keyboard(user_id))
        return

    if "CHILE" in text or "CL" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "cl"}})
        await update.message.reply_text("✅ Country set: CHILE (CL) 🇨🇱", reply_markup=get_main_keyboard(user_id))
        return

    # SERVICE SELECTION
    if text == "📱 𝚂𝙴𝚃 𝚂𝙴𝚁𝚅𝙸𝙲𝙴":
        service_kb = [
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲𝙴: TG (𝚃𝙴𝙻𝙴𝙶𝚁𝙰𝙼)")],
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲𝙴: WA (𝚆𝙷𝙰𝚃𝚂𝙰𝙿𝙿)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text("📱 SELECT YOUR SERVICE:", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if "TG" in text or "TELEGRAM" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "tg"}})
        await update.message.reply_text("✅ SERVICE SET: TELEGRAM (TG)", reply_markup=get_main_keyboard(user_id))
        return

    if "WA" in text or "WHATSAPP" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "wa"}})
        await update.message.reply_text("✅ SERVICE SET: WHATSAPP (WA)", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄":
        await start(update, context)
        return

    if text == "🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁":
        user_has_active = any(order.get("user_id") == user_id for order in active_orders.values())
        if user_has_active:
            await update.message.reply_text(
                "⚠️ অলরেডি একটি নম্বর কেনা রয়েছে!\nনতুন নম্বর কেনার আগে আগের নম্বরটি ব্যবহার সম্পন্ন করুন অথবা Cancel করুন."
            )
            return

        country = u_data.get("selected_country", "hk")
        service = u_data.get("selected_service", "tg")
        country_flag = get_country_flag(country)
        
        if country == "hk" and service == "wa":
            max_price_limit = 0.07
        elif country == "cl" and service == "wa":
            max_price_limit = 0.079
        elif country == "cl" and service == "tg":
            max_price_limit = 0.087
        elif country == "cl":
            max_price_limit = 0.087
        else:
            max_price_limit = 0.075
        
        bot_rate = get_rate(service_code=service, country_code=country)
        user_bal = u_data.get("balance", 0.0)

        if user_bal < bot_rate:
            await update.message.reply_text(
                f"❌ SORRY NOT ENOUGH BALANCE: {bot_rate} USDT, YOUR BALANCE: {user_bal:.4f} USDT.\nPLEASE DEPOSIT."
            )
            return

        status_msg = await update.message.reply_text(f"⏳ {country.upper()} {country_flag} BUYING NUMBER... WAIT A FEW SECONDS.")

        res = buy_vak_number(service=service, country=country, max_price=max_price_limit)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            raw_phone = str(res["tel"])
            phone_num = f"+{raw_phone}" if not raw_phone.startswith("+") else raw_phone
            id_num = str(res["idNum"])

            inline_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Check Active OTP", callback_data=f"check_otp_{id_num}")],
                [InlineKeyboardButton("🚫 Cancel Number", callback_data=f"cancel_num_{id_num}")]
            ])

            sent_msg = await update.message.reply_text(
                f"✅ NUMBER PURCHASED SUCCESSFUL!\n\n"
                f"📱 Number: {phone_num}\n"
                f"🆔 ID Num: {id_num}\n"
                f"🌍 Country: {country.upper()} {country_flag}\n"
                f"💬 Service: {service.upper()}\n"
                f"💵 Rate: {bot_rate} USDT (OTP ASLEI BALANCE KATBE)\n\n"
                f"⏳ OTP POWER JONNO OPEK KORUN...",
                reply_markup=inline_kb
            )

            active_orders[id_num] = {
                "user_id": user_id,
                "service": service,
                "country": country,
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
            err_msg = res.get("error", "Stock Out!") if isinstance(res, dict) else "Stock Out!"
            await update.message.reply_text(f"❌ {err_msg}")
        return

    if text == "⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻" and user_id == ADMIN_ID:
        await send_admin_panel(update, context)
        return

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "🟢 ON (Active)" if is_bot_active() else "🔴 OFF (Maintenance)"
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 VIEW ALL USER", callback_data="admin_view_users"), InlineKeyboardButton("📊 DAILY STATS", callback_data="admin_daily_stats")],
        [InlineKeyboardButton("🚫 BAN USER", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
        [InlineKeyboardButton("💵 SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("💵 SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
        [InlineKeyboardButton("💵 SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("💵 SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("🔄 ZERO BALANCE", callback_data="admin_zero_bal_start")],
        [InlineKeyboardButton("📢 BROADCAST ALL", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(f"BOT STATUS: {status_str}", callback_data="admin_toggle_bot")]
    ])
    if update.message:
        await update.message.reply_text("🛠 Admin Control Panel:", reply_markup=admin_kb)
    elif update.callback_query:
        await update.callback_query.message.reply_text("🛠 Admin Control Panel:", reply_markup=admin_kb)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "admin_view_users" and user_id == ADMIN_ID:
        try:
            now = datetime.now()
            subscribed_users = list(users_col.find({
                "subscription_expiry": {"$gt": now}
            }))
            
            if not subscribed_users:
                await query.message.reply_text("📋 Currently, there are no active subscribed users.")
                return
            
            msg = f"👥 Active Subscribed Users ({len(subscribed_users)}):\n\n"
            for u in subscribed_users:
                uid = u.get("user_id", "N/A")
                safe_name = str(u.get("full_name", "User")).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
                bal = u.get("balance", 0.0)
                otp_cnt = u.get("otp_count", 0)
                msg += f"• {safe_name} ({uid})\n  └ 💰 Balance: {bal:.4f} USDT | 📩 OTP Rcv: {otp_cnt}\n\n"
                
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"❌ Error loading users: {str(e)}")

    elif data == "admin_daily_stats" and user_id == ADMIN_ID:
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            logs = list(otp_logs_col.find({"date": today_str}))
            
            total_today = len(logs)
            hk_count = sum(1 for log in logs if log.get("country", "").lower() == "hk")
            cl_count = sum(1 for log in logs if log.get("country", "").lower() == "cl")
            other_count = total_today - (hk_count + cl_count)
            
            stats_msg = (
                f"📊 TODAY OTP RECEIVED STATS ({today_str}):\n\n"
                f"📱 Total OTP Received Today: {total_today}\n"
                f"🇭🇰 Hong Kong (HK): {hk_count}\n"
                f"🇨🇱 Chile (CL): {cl_count}\n"
                f"🌐 Others: {other_count}"
            )
            await query.message.reply_text(stats_msg)
        except Exception as e:
            await query.message.reply_text(f"❌ Error generating stats: {str(e)}")

    elif data == "admin_toggle_bot" and user_id == ADMIN_ID:
        current_status = is_bot_active()
        new_status = not current_status
        set_bot_active(new_status)
        status_text = "🟢 Bot ON (Active) kora hoyeche!" if new_status else "🔴 Bot OFF (Maintenance Mode) kora hoyeche!"
        
        status_str = "🟢 ON (Active)" if new_status else "🔴 OFF (Maintenance)"
        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 VIEW ALL USER", callback_data="admin_view_users"), InlineKeyboardButton("📊 DAILY STATS", callback_data="admin_daily_stats")],
            [InlineKeyboardButton("🚫 BAN USER", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
            [InlineKeyboardButton("💵 SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("💵 SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
            [InlineKeyboardButton("💵 SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("💵 SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
            [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("🔄 ZERO BALANCE", callback_data="admin_zero_bal_start")],
            [InlineKeyboardButton("📢 BROADCAST ALL", callback_data="admin_broadcast_start")],
            [InlineKeyboardButton(f"BOT STATUS: {status_str}", callback_data="admin_toggle_bot")]
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=admin_kb)
        except Exception:
            pass
        await query.message.reply_text(status_text)

    elif data.startswith("check_otp_"):
        id_num = data.split("_")[2]
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            await process_otp_success(context, id_num, otp)
        else:
            await query.message.reply_text("⏳ AKHONO OTP ASENI, AKTU PORE ABAR TRY KORUN.")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        if id_num in active_orders:
            set_number_status(id_num, "bad")
            active_orders.pop(id_num, None)
            try:
                await query.message.delete()
            except Exception:
                pass
        else:
            try:
                await query.message.delete()
            except Exception:
                await query.message.reply_text("❌ ORDER NOT ACTIVE OR OTP ALREADY RECEIVED.")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        users_col.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ Approved & Balance Added!")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 Apnar {amount} USDT deposit shofolbhabe jukto kora hoyeche!")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ Deposit Rejected!")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        user_doc = users_col.find_one({"user_id": target_id})
        days = user_doc.get("sub_days", 5) if user_doc else 5
        expiry_date = datetime.now() + timedelta(days=days)
        users_col.update_one({"user_id": target_id}, {"$set": {"subscription_expiry": expiry_date}})
        await query.edit_message_caption(caption=query.message.caption + f"\n\n✅ Subscription Approved ({days} Days Active)!")
        
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎉 Apnar Subscription Approved hoyeche! {days} Diner jonno bot-er sob features active kora hoyeche.",
            reply_markup=get_main_keyboard(target_id)
        )

    elif data.startswith("reject_sub_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ Subscription Rejected!")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar subscription request-ti batil kora hoyeche.")

async def process_otp_success(context, id_num: str, otp: str):
    if id_num not in active_orders:
        return

    order_info = active_orders.pop(id_num)
    uid = order_info["user_id"]
    cost = order_info["cost"]
    phone = order_info["phone"]
    msg_id = order_info["msg_id"]
    service_type = order_info.get("service", "tg").upper()
    country_code = order_info.get("country", "hk")
    country_flag = get_country_flag(country_code)

    users_col.update_one(
        {"user_id": uid},
        {"$inc": {"balance": -cost, "otp_count": 1}}
    )

    try:
        otp_logs_col.insert_one({
            "user_id": uid,
            "country": country_code,
            "service": service_type,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now()
        })
    except Exception as e:
        logging.error(f"Failed to log OTP: {e}")
    
    updated_user = get_user(uid)
    rem_bal = updated_user.get("balance", 0.0) if updated_user else 0.0
    set_number_status(id_num, "end")

    success_text = (
        f"✅ OTP RECEIVE SUCCESSFUL!\n\n"
        f"📱 NUMBER: {phone}\n"
        f"🔑 OTP CODE: {otp}\n\n"
        f"💵 BALANCE DEDUCTED: {cost} USDT\n"
        f"💰 REMAINING BALANCE: {rem_bal:.4f} USDT"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=uid,
            message_id=msg_id,
            text=success_text
        )
    except Exception:
        await context.bot.send_message(chat_id=uid, text=success_text)

    masked_phone = mask_number(phone)
    group_forward_msg = (
        f"🌐 COUNTRY: {country_code.upper()} {country_flag}\n"
        f"📱 NUMBER: {masked_phone}\n"
        f"🔑 OTP: {otp}\n"
        f"💬 Message: YOUR {service_type} CODE: {otp}"
    )

    if OTP_GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=OTP_GROUP_ID,
                text=group_forward_msg
            )
            logging.info(f"OTP Forwarded to Group {OTP_GROUP_ID} successfully.")
        except Exception as e:
            logging.error(f"Failed to forward OTP to group: {e}")

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

async def sub_start_5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    users_col.update_one({"user_id": user_id}, {"$set": {"sub_days": 5}})
    
    pay_methods_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 BKASH (50 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.40 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("🚫 CANCEL", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 PAYMENT METHOD SELECT KORUN (Plan: 5 Days - 50 Tk / 0.40 USDT):", reply_markup=pay_methods_kb)
    return SUB_METHOD

async def sub_start_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    users_col.update_one({"user_id": user_id}, {"$set": {"sub_days": 7}})
    
    pay_methods_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 BKASH (70 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.56 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("🚫 CANCEL", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 PAYMENT METHOD SELECT KORUN (Plan: 7 Days - 70 Tk / 0.56 USDT):", reply_markup=pay_methods_kb)
    return SUB_METHOD

async def sub_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    user_doc = users_col.find_one({"user_id": user_id})
    days = user_doc.get("sub_days", 5) if user_doc else 5
    
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    
    if data == "pay_method_bkash":
        users_col.update_one({"user_id": user_id}, {"$set": {"sub_method": "bkash"}})
        price = 50 if days == 5 else 70
        msg = (
            f"💰 PLAN: {days} Days\n"
            f"💰 AMOUNT: {price} Tk\n\n"
            f"👇 SEND BKASH PERSONAL NUMBER:\n"
            f"📱 BKASH NUMBER: {ADMIN_BKASH}\n\n"
            f"Taka dea sese TrxID-ti likhe pathan:"
        )
        await query.message.reply_text(msg, reply_markup=cancel_kb)
        return SUB_TXID
        
    elif data == "pay_method_binance":
        users_col.update_one({"user_id": user_id}, {"$set": {"sub_method": "binance"}})
        usdt_amt = 0.40 if days == 5 else 0.56
        msg = (
            f"💰 PLAN: {days} Days\n"
            f"💰 AMOUNT: {usdt_amt} USDT (Rate: 125 Tk/$)\n\n"
            f"👇 SEND BINANCE PAY ID:\n"
            f"🆔 BINANCE PAY ID: {BINANCE_ID}\n\n"
            f"Dollar send korar por apnar Order ID / TxID-ti likhe pathan:"
        )
        await query.message.reply_text(msg, reply_markup=cancel_kb)
        return SUB_TXID

async def sub_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["sub_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("📸 PAYMENT SCREENSHOT (Photo) DIN:", reply_markup=cancel_kb)
    return SUB_SCREENSHOT

async def sub_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    txid = context.user_data.get("sub_txid")
    user_doc = users_col.find_one({"user_id": user.id})
    days = user_doc.get("sub_days", 5) if user_doc else 5
    method = user_doc.get("sub_method", "bkash").upper()
    
    if method == "BKASH":
        price_str = f"{50 if days == 5 else 70} Tk"
    else:
        price_str = f"{0.40 if days == 5 else 0.56} USDT"

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ APPROVED", callback_data=f"approve_sub_{user.id}"),
            InlineKeyboardButton("❌ REJECTED", callback_data=f"reject_sub_{user.id}")
        ]
    ])

    caption = (
        f"🔔 NEW SUBSCRIPTION REQUEST!\n\n"
        f"👤 USER: {user.full_name} ({user.id})\n"
        f"💳 METHOD: {method}\n"
        f"📅 PLAN: {days} Days\n"
        f"💰 AMOUNT: {price_str}\n"
        f"🧾 TRXID / ORDER ID: {txid}"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, reply_markup=admin_kb)
    await update.message.reply_text("✅ Apnar subscription request admin-er kache pathano hoyeche! Admin approve korlei bot active hoye jaabe.")
    return ConversationHandler.END

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💛 Binance Pay", callback_data="pay_binance")],
        [InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]
    ])
    await update.message.reply_text("💳 Payment Method select korunk:", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def deposit_binance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    await query.message.reply_text("📥 Apni koto USDT pathaben ta likhe janan (Minimum: 1 USDT, jemon: 1, 2.5, 5):", reply_markup=cancel_kb)
    return WAITING_AMOUNT

async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount < 1.0:
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
            await update.message.reply_text("❌ Minimum deposit amount 1 USDT. Doya kore 1 ba tar besi amount likhun.", reply_markup=cancel_kb)
            return WAITING_AMOUNT

        context.user_data["dep_amount"] = amount
        
        msg = (
            f"💰 Deposit Amount: {amount} USDT\n\n"
            f"👇 Nicher Binance Pay ID-te Binance app theke Pay/Send Money Korun:\n"
            f"🆔 Binance Pay ID: {BINANCE_ID}\n\n"
            f"⚠️ Note: Minimum deposit 1 USDT. Binance Pay-er madhyome kono extra fee charai pathano jabe.\n\n"
            f"Dollar pathanor por apnar Order ID / TxID-ti likhe message din:"
        )
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text(msg, reply_markup=cancel_kb)
        return WAITING_TXID
    except ValueError:
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text("❌ Sothik shongkha likhun (jemon: 1 ba 5).", reply_markup=cancel_kb)
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("📸 Ekhon apnar payment-er screenshot (Photo) Pathan:", reply_markup=cancel_kb)
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
        f"📥 Notun Deposit Request!\n\n"
        f"👤 User: {user.full_name} ({user.id})\n"
        f"💰 Amount: {amount} USDT\n"
        f"🧾 TxID: {txid}"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, reply_markup=admin_kb)
    await update.message.reply_text("✅ Apnar deposit request admin-er kache pathano hoyeche! Jaachai kore druto balance jukto kora hobe.")
    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text("❌ Process batil kora hoyeche.")
    return ConversationHandler.END

async def admin_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🚫 Banned korte chawa User ID-ti likhe pathan:")
    return ADMIN_BAN

async def admin_ban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"✅ User {uid}-ke banned kora hoyeche.")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
    return ConversationHandler.END

async def admin_unban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✅ Unban korte chawa User ID-ti likhe pathan:")
    return ADMIN_UNBAN

async def admin_unban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
        await update.message.reply_text(f"✅ User {uid}-ke unban kora hoyeche.")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
    return ConversationHandler.END

async def admin_add_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("➕ Balance add korte chawa User ID-ti pathan:")
    return ADMIN_ADD_BAL_USER

async def admin_add_bal_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        context.user_data["target_add_uid"] = uid
        await update.message.reply_text(f"💰 User {uid}-er jonno koto USDT balance add korben ta likhun:")
        return ADMIN_ADD_BAL_AMT
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
        return ConversationHandler.END

async def admin_add_bal_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.strip())
        uid = context.user_data.get("target_add_uid")
        users_col.update_one({"user_id": uid}, {"$inc": {"balance": amt}})
        
        u = get_user(uid)
        new_bal = u.get("balance", 0.0) if u else amt
        
        await update.message.reply_text(f"✅ Successfully added {amt} USDT to User {uid}. Notun Balance: {new_bal:.4f} USDT")
        await context.bot.send_message(chat_id=uid, text=f"🎉 Admin apnar account-e {amt} USDT balance add koreche!")
    except ValueError:
        await update.message.reply_text("❌ Invalid Amount.")
    return ConversationHandler.END

async def admin_zero_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🔄 Je user-er balance 0 (zero) korte chan, tar User ID-ti pathan:")
    return ADMIN_ZERO_BAL_USER

async def admin_zero_bal_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        result = users_col.update_one({"user_id": uid}, {"$set": {"balance": 0.0}})
        
        if result.matched_count > 0:
            await update.message.reply_text(f"✅ Successfully User {uid}-er balance 0 USDT kora hoyeche.")
            try:
                await context.bot.send_message(chat_id=uid, text="⚠️ Admin apnar account-er balance 0 kore diyeche.")
            except Exception:
                pass
        else:
            await update.message.reply_text(f"❌ Database-e {uid} ID-er kono user pawa jayni.")
            
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID! Sothik shongkha likhun.")
    return ConversationHandler.END

async def admin_rate_wa_hk_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 Hong Kong (HK) WhatsApp (WA)-er notun Bot Rate USDT-te likhun (jemon: 0.075 ba 0.10):")
    return ADMIN_RATE_WA_HK_SET

async def admin_rate_wa_hk_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("wa", "hk", rate)
        await update.message.reply_text(f"✅ Hong Kong WA Rate update kora hoyeche: {rate} USDT")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate Format!")
    return ConversationHandler.END

async def admin_rate_wa_cl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 Chile (CL) WhatsApp (WA)-er notun Bot Rate USDT-te likhun (jemon: 0.087 ba 0.10):")
    return ADMIN_RATE_WA_CL_SET

async def admin_rate_wa_cl_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("wa", "cl", rate)
        await update.message.reply_text(f"✅ Chile WA Rate update kora hoyeche: {rate} USDT")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate Format!")
    return ConversationHandler.END

async def admin_rate_tg_hk_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 Hong Kong (HK) Telegram (TG)-er notun Bot Rate USDT-te likhun (jemon: 0.10 ba 0.12):")
    return ADMIN_RATE_TG_HK_SET

async def admin_rate_tg_hk_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("tg", "hk", rate)
        await update.message.reply_text(f"✅ Hong Kong TG Rate update kora hoyeche: {rate} USDT")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate Format!")
    return ConversationHandler.END

async def admin_rate_tg_cl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 Chile (CL) Telegram (TG)-er notun Bot Rate USDT-te likhun (jemon: 0.10 ba 0.12):")
    return ADMIN_RATE_TG_CL_SET

async def admin_rate_tg_cl_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("tg", "cl", rate)
        await update.message.reply_text(f"✅ Chile TG Rate update kora hoyeche: {rate} USDT")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate Format!")
    return ConversationHandler.END

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    await query.message.reply_text("📢 Sobai ke broadcast korte chawa message-ti (Text/Photo) ekhane pathan:", reply_markup=cancel_kb)
    return ADMIN_BROADCAST

async def admin_broadcast_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    all_users = list(users_col.find())
    success_count = 0
    fail_count = 0
    
    status_msg = await update.message.reply_text(f"⏳ Broadcast Process Shuru Hoche... Total Users: {len(all_users)}")
    
    for u in all_users:
        uid = u.get("user_id")
        if not uid:
            continue
        try:
            if update.message.photo:
                photo_file_id = update.message.photo[-1].file_id
                caption_text = update.message.caption or ""
                caption_entities = update.message.caption_entities
                await context.bot.send_photo(
                    chat_id=uid, 
                    photo=photo_file_id, 
                    caption=caption_text,
                    caption_entities=caption_entities
                )
            else:
                text_content = update.message.text or ""
                text_entities = update.message.entities
                await context.bot.send_message(
                    chat_id=uid, 
                    text=text_content,
                    entities=text_entities
                )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    result_text = (
        f"📢 Broadcast Shes Huyeche!\n\n"
        f"✅ Success: {success_count} Users\n"
        f"❌ Failed/Blocked: {fail_count} Users"
    )
    await status_msg.edit_text(result_text)
    return ConversationHandler.END

# Async Main Runner
async def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    sub_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(sub_start_5, pattern="^buy_sub_5$"),
            CallbackQueryHandler(sub_start_7, pattern="^buy_sub_7$")
        ],
        states={
            SUB_METHOD: [
                CallbackQueryHandler(sub_method_selected, pattern="^pay_method_(bkash|binance)$")
            ],
            SUB_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_txid_received)],
            SUB_SCREENSHOT: [MessageHandler(filters.PHOTO, sub_screenshot_received)]
        },
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💵 𝙳𝙸𝙿𝙾𝚂𝙸𝚃$"), deposit_start)],
        states={
            WAITING_AMOUNT: [
                CallbackQueryHandler(deposit_binance_selected, pattern="^pay_binance$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount_received)
            ],
            WAITING_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_txid_received)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, deposit_screenshot_received)]
        },
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_ban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_start, pattern="^admin_ban_start$")],
        states={ADMIN_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_unban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_start, pattern="^admin_unban_start$")],
        states={ADMIN_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_add_bal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_bal_start, pattern="^admin_add_bal_start$")],
        states={
            ADMIN_ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_user)],
            ADMIN_ADD_BAL_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_amt)]
        },
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_zero_bal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_zero_bal_start, pattern="^admin_zero_bal_start$")],
        states={ADMIN_ZERO_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_zero_bal_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_rate_wa_hk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_wa_hk_start, pattern="^admin_rate_wa_hk_start$")],
        states={ADMIN_RATE_WA_HK_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_wa_hk_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_rate_wa_cl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_wa_cl_start, pattern="^admin_rate_wa_cl_start$")],
        states={ADMIN_RATE_WA_CL_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_wa_cl_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_rate_tg_hk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_tg_hk_start, pattern="^admin_rate_tg_hk_start$")],
        states={ADMIN_RATE_TG_HK_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_tg_hk_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_rate_tg_cl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_tg_cl_start, pattern="^admin_rate_tg_cl_start$")],
        states={ADMIN_RATE_TG_CL_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_tg_cl_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast_start$")],
        states={ADMIN_BROADCAST: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, admin_broadcast_process)]},
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(sub_conv)
    app.add_handler(deposit_conv)
    app.add_handler(admin_ban_conv)
    app.add_handler(admin_unban_conv)
    app.add_handler(admin_add_bal_conv)
    app.add_handler(admin_zero_bal_conv)
    app.add_handler(admin_rate_wa_hk_conv)
    app.add_handler(admin_rate_wa_cl_conv)
    app.add_handler(admin_rate_tg_hk_conv)
    app.add_handler(admin_rate_tg_cl_conv)
    app.add_handler(admin_broadcast_conv)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    async with app:
        await app.start()
        await app.updater.start_polling()
        logging.info("🤖 Bot startup sequence completed. Polling started successfully.")
        await asyncio.Event().wait()

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()

if __name__ == "__main__":
    main()
