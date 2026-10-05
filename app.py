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
        await update.message.reply_text("❌ 𝙱𝙰𝙽 𝙱𝚈 𝙰𝙳𝙼𝙸𝙽 𝙲𝙾𝙽𝚃𝙰𝙲𝚃 𝙰𝙳𝙼𝙸𝙽.", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 **ʙᴏᴛ ᴜɴᴅᴇʀ ᴍᴀɪɴᴛᴀɪɴɪɴɢ ʙʏ ᴀᴅᴍɪɴ.** ᴘʟᴇᴀsᴇ ᴛʀʏ sᴏᴍᴇ ᴛɪᴍᴇ.", parse_mode="Markdown")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        msg = (
            f"👋 **Hello {user.full_name}!**\n\n"
            f"❌ 𝚈𝙾𝚄 𝙳𝙾𝙽'𝚃 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾𝙽 𝚃𝙷𝙴 𝙱𝙾𝚃!\n"
            f"ʙᴏᴛ ʙᴇʙᴏʜᴀʀ ᴋᴏʀᴛᴇ ᴄʜᴀɪʟᴇ sᴜʙsᴄʀɪᴘᴛɪᴏɴ ɴɪᴛᴇ ʜᴏʙᴇ.\n\n"
            f"📌 **𝗣𝗥𝗜𝗖𝗘 & 𝗩𝗔𝗟𝗜𝗗𝗜𝗧𝗬 (Rate: 125 Tk/$) :**\n"
            f"• `5 Days` = **50 Tk** (or `0.40 USDT`)\n"
            f"• `7 Days` = **70 Tk** (or `0.56 USDT`)\n\n"
            f"ɴɪᴄʜᴇʀ ʙᴜᴛᴛᴏɴ ᴛʜᴇᴋᴇ ᴄʟɪᴄᴋ ᴋᴏʀᴇ sᴜʙsᴄ𝚁𝙸𝙿𝚃𝙸𝙾𝙽 ᴋɪɴᴜɴ:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 **𝙱𝚄𝚈 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾Ն:**", reply_markup=sub_kb)
        return

    exp_time = u_data.get("subscription_expiry")
    exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"

    curr_country_code = u_data.get("selected_country", "hk")
    curr_country = curr_country_code.upper()
    country_flag = get_country_flag(curr_country_code)
    curr_service = u_data.get("selected_service", "tg").upper()

    welcome_msg = (
        f"👋 **𝚆𝙴𝙻𝙲𝙾𝙼𝙴 𝚁𝙴𝚇 𝙿𝚁𝙸𝚅𝙰𝚃𝙴 𝙱𝙾𝚃!**\n\n"
        f"⚙️ **𝚁𝙴𝙲𝙴𝙽𝚃 𝚂𝙴𝚃𝚄𝙿:**\n"
        f"• 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂: `{curr_country}` {country_flag}\n"
        f"• Service: `{curr_service}`\n"
        f"• 𝚈𝙾𝚄𝚁 𝙱𝙰𝙻𝙰𝙽𝙲𝙴: `${u_data.get('balance', 0.0):.4f} USDT`\n"
        f"• 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾𝙽 𝚅𝙰𝙻𝙸𝙳 𝚃𝙸𝙻𝙻: `{exp_str}`\n\n"
        f"𝙺𝙰𝙹 𝙺𝙾𝚁𝚃𝙴 𝙽𝙸𝙲𝙷𝙴 𝙳𝙴𝙰 𝙼𝙴𝙽𝚄 𝚄𝚂𝙴 𝙺𝙾𝚁𝙴𝙽:"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text("❌ 𝚈𝙾𝚄𝚁 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙷𝙰𝚂 𝙱𝙴𝙴𝙽 𝙱𝙰𝙽 𝙱𝚈 𝙰𝙳𝙼𝙸𝙽.", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 **𝙱𝙾𝚃 𝚄𝙽𝙳𝙴𝚁 𝙼𝙰𝙸𝙽𝚃𝙰𝙸𝙽𝚂 𝙱𝚈 𝙰𝙳𝙼𝙸𝙽.** 𝚃𝚁𝙸 𝚂𝙾𝙼𝙴 𝚃𝙸𝙼𝙴 𝙰𝙶𝙰𝙸𝙽.", parse_mode="Markdown")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        await update.message.reply_text("❌ 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾𝙽 𝙴𝚇𝙿𝙸𝚁𝙴𝚂! 𝙱𝚄𝚈 𝙽𝙴𝚆 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾𝙽.", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 **𝙱𝚄𝚈 𝚂𝚄𝙱𝚂𝙲𝚁𝙸𝙿𝚃𝙸𝙾𝙽:**", reply_markup=sub_kb)
        return

    text = update.message.text.strip()

    if text == "💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"💰 **𝙼𝚈 𝙱𝙰𝙻𝙰𝙽𝙲𝙴:** `${bot_bal:.4f}` USDT"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n🏦 **𝙿𝙰𝙽𝙴𝙻 𝙱𝙰𝙻𝙰𝙽𝙲𝙴 :** `${site_bal:.4f}` USD"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if text == "👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴":
        bot_bal = u_data.get("balance", 0.0)
        otp_cnt = u_data.get("otp_count", 0)
        exp_time = u_data.get("subscription_expiry")
        exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"
        profile_msg = (
            f"👤 **Apnar Profile Info:**\n\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"📛 **Name:** {user.full_name}\n"
            f"💵 **Balance:** `${bot_bal:.4f}` USDT\n"
            f"📩 **Total OTP Received:** `{otp_cnt}`\n"
            f"📅 **Subscription Valid:** `{exp_str}`"
        )
        await update.message.reply_text(profile_msg, parse_mode="Markdown")
        return

    # COUNTRY SELECTION
    if text in ["🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝚈", "🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂"]:
        country_kb = [
            [KeyboardButton("COUNTRY: HK 🇭🇰 (HONG KONG)"), KeyboardButton("COUNTRY: CHILE 🇨🇱 (CL)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text("🌐 **SELECT YOUR COUNTRY:**", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if "HK" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "hk"}})
        await update.message.reply_text("✅ Country set: `HONG KONG (HK)` 🇭🇰", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    if "CHILE" in text or "CL" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "cl"}})
        await update.message.reply_text("✅ Country set: `CHILE (CL)` 🇨🇱", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    # SERVICE SELECTION
    if text == "📱 𝚂𝙴𝚃 𝚂𝙴𝚁𝚅𝙸𝙲𝙴":
        service_kb = [
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲𝙴: TG (𝚃𝙴𝙻𝙴𝙶𝚁𝙰𝙼)")],
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲𝙴: WA (𝚆𝙷𝙰𝚃𝚂𝙰𝙿𝙿)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text("📱 **SELECT YOUR SERVICE:**", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if "TG" in text or "TELEGRAM" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "tg"}})
        await update.message.reply_text("✅ 𝚂𝙴𝚁𝚅𝙸𝙲𝙴 𝚂𝙴𝚃: `TELEGRAM (TG)`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    if "WA" in text or "WHATSAPP" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "wa"}})
        await update.message.reply_text("✅ 𝚂𝙴𝚁𝚅𝙸𝙲𝙴 𝚂𝙴𝚃: `WHATSAPP (WA)`", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄":
        await start(update, context)
        return

    if text == "🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁":
        user_has_active = any(order.get("user_id") == user_id for order in active_orders.values())
        if user_has_active:
            await update.message.reply_text(
                "⚠️ **অলরেডি একটি নম্বর কেনা রয়েছে!**\nনতুন নম্বর কেনার আগে আগের নম্বরটি ব্যবহার সম্পন্ন করুন অথবা Cancel করুন."
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
                f"❌ 𝚂𝙾𝚁𝚁𝚈 𝙳𝙾 𝙽𝙾𝚃𝙴 𝙰𝙽𝙰𝙵 𝙱𝙰𝙻𝙰𝙽𝙲𝙴: `${bot_rate}` USDT, 𝚈𝙾𝚄𝚁 𝙱𝙰𝙻𝙰𝙽𝙲𝙴: `${user_bal:.4f}` USDT.\n𝙳𝙸𝙿𝙾𝚂𝙸𝚃 𝙺𝙾𝚁𝚄𝙽."
            )
            return

        status_msg = await update.message.reply_text(f"⏳ `{country.upper()}` {country_flag} BUYING NUMBER... WAIT A FEW SECONDS.")

        res = buy_vak_number(service=service, country=country, max_price=max_price_limit)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            raw_phone = str(res["tel"])
            phone_num = f"+{raw_phone}" if not raw_phone.startswith("+") else raw_phone
            id_num = str(res["idNum"])

            inline_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📩 Check Active OTP", callback_data=f"check_otp_{id_num}")],
                [InlineKeyboardButton("❌ Cancel Number", callback_data=f"cancel_num_{id_num}")]
            ])

            sent_msg = await update.message.reply_text(
                f"✅ **𝙽𝚄𝙼𝙱𝙴𝚁 𝙱𝚄𝙸𝙻𝙳 𝚂𝚄𝙲𝙲𝙴𝚂𝚂𝙵𝚄𝚈!**\n\n"
                f"📱 **Number:** `<code>{phone_num}</code>`\n"
                f"🆔 **ID Num:** `{id_num}`\n"
                f"🌍 **Country:** `{country.upper()}` {country_flag}\n"
                f"💬 **Service:** `{service.upper()}`\n"
                f"💵 **Rate:** `${bot_rate}` USDT *(𝙊𝙏𝙋 𝘼𝙎𝙇𝙀𝙄 𝘽𝘼𝙇𝘼𝙉𝙲𝙀 𝙆𝘼𝙏𝘽𝙀)*\n\n"
                f"⏳ *𝙾𝚃𝙿 𝙿𝙾𝚆𝙴𝚁 𝙹𝙾𝙽𝙽𝙾 𝙾𝙿𝙴𝙺𝙺𝙷𝙰 𝙺𝙾𝚁𝚄𝙽...*",
                parse_mode="HTML",
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
            await update.message.reply_text(f"❌ `{err_msg}`")
        return

    if text == "⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻" and user_id == ADMIN_ID:
        await send_admin_panel(update, context)
        return

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "🟢 ON (Active)" if is_bot_active() else "🔴 OFF (Maintenance)"
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 𝗩𝗜𝗘𝗪 𝗔𝗟𝗟 𝗨𝗦𝗘𝗥", callback_data="admin_view_users")],
        [InlineKeyboardButton("🚫 𝗕𝗔𝗡 𝗨𝗦𝗘𝗥", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
        [InlineKeyboardButton("💵 SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("💵 SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
        [InlineKeyboardButton("💵 SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("💵 SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
        [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("🔄 𝗭𝗘𝗥𝗢 𝗕𝙰𝙻𝙰𝙽𝙲𝙴", callback_data="admin_zero_bal_start")],
        [InlineKeyboardButton("📢 𝗕𝗥𝗢𝙳𝙲𝙰𝚂𝚃 𝙰𝙻𝙻", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(f"𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗨𝗦: {status_str}", callback_data="admin_toggle_bot")]
    ])
    if update.message:
        await update.message.reply_text("🛠 **Admin Control Panel:**", reply_markup=admin_kb, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text("🛠 **Admin Control Panel:**", reply_markup=admin_kb, parse_mode="Markdown")

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
            
            msg = f"👥 **Active Subscribed Users ({len(subscribed_users)}):**\n\n"
            for u in subscribed_users:
                uid = u.get("user_id", "N/A")
                raw_name = str(u.get("full_name", "User"))
                safe_name = raw_name.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
                bal = u.get("balance", 0.0)
                otp_cnt = u.get("otp_count", 0)
                msg += f"• **{safe_name}** (`{uid}`)\n  └ 💰 Balance: `${bal:.4f}` USDT | 📩 OTP Rcv: `{otp_cnt}`\n\n"
                
            await query.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ Error loading users: {str(e)}")

    elif data == "admin_toggle_bot" and user_id == ADMIN_ID:
        current_status = is_bot_active()
        new_status = not current_status
        set_bot_active(new_status)
        status_text = "🟢 **Bot ON (Active) kora hoyeche!**" if new_status else "🔴 **Bot OFF (Maintenance Mode) kora hoyeche!**"
        
        status_str = "🟢 ON (Active)" if new_status else "🔴 OFF (Maintenance)"
        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 𝗩𝗜𝗘𝗪 𝗔𝗟𝗟 𝗨𝗦𝗘𝗥", callback_data="admin_view_users")],
            [InlineKeyboardButton("🚫 𝗕𝗔𝗡 𝗨𝗦𝗘𝗥", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
            [InlineKeyboardButton("💵 SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("💵 SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
            [InlineKeyboardButton("💵 SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("💵 SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
            [InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("🔄 𝗭𝗘𝗥𝗢 𝗕𝙰𝙻𝙰𝙽𝙲𝙴", callback_data="admin_zero_bal_start")],
            [InlineKeyboardButton("📢 𝗕𝗥𝗢𝙳𝙲𝙰𝚂𝚃 𝙰𝙻𝙻", callback_data="admin_broadcast_start")],
            [InlineKeyboardButton(f"𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗨𝗦: {status_str}", callback_data="admin_toggle_bot")]
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=admin_kb)
        except Exception:
            pass
        await query.message.reply_text(status_text, parse_mode="Markdown")

    elif data.startswith("check_otp_"):
        id_num = data.split("_")[2]
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            await process_otp_success(context, id_num, otp)
        else:
            await query.message.reply_text("⏳ 𝙰𝙺𝙷𝙾𝙽𝙾 𝙾𝚃𝙿 𝙰𝚂𝙴𝙽𝙸, 𝙰𝙺𝚃𝚄 𝙿𝙾𝚁𝙴 𝙰𝙱𝙰𝚁 𝚃𝚁𝙸 𝙺𝙾𝚁𝚄𝙽.")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        if id_num in active_orders:
            set_number_status(id_num, "bad")
            active_orders.pop(id_num, None)
            try:
                await query.edit_message_text(
                    "❌ **𝙽𝚄𝙼𝙱𝙴𝚁 𝙲𝙰𝙽𝙲𝙴𝙻𝙴𝙳(𝙱𝙰𝙻𝙰𝙽𝙲𝙴 𝙺𝙰𝚃𝙰 𝙷𝙾𝚈𝙽𝙸).**",
                    reply_markup=None
                )
            except Exception:
                await query.message.delete()
        else:
            await query.message.reply_text("❌ 𝙳𝙾𝙽'𝚃 𝙰𝙲𝚃𝙸𝚅𝙴 𝙾𝚁𝙳𝙴𝚁 𝙽𝙰𝙷𝙾𝙻𝙴 𝙾𝚃𝙿 𝙰𝙻𝚁𝙴𝙰𝙳𝚈 𝚁𝙴𝙲𝙴𝙸𝚅𝙴𝙳 𝙺𝙾𝚁𝙰 𝙷𝙾𝙸𝙲𝙷𝙴.")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        users_col.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Approved & Balance Added!**")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 **Apnar `${amount}` USDT deposit shofolbhabe jukto kora hoyeche!**")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Deposit Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        user_doc = users_col.find_one({"user_id": target_id})
        days = user_doc.get("sub_days", 5) if user_doc else 5
        expiry_date = datetime.now() + timedelta(days=days)
        users_col.update_one({"user_id": target_id}, {"$set": {"subscription_expiry": expiry_date}})
        await query.edit_message_caption(caption=query.message.caption + f"\n\n✅ **Subscription Approved ({days} Days Active)!**")
        
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎉 **Apnar Subscription Approved hoyeche!** {days} Diner jonno bot-er sob features active kora hoyeche.",
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
    service_type = order_info.get("service", "tg").upper()
    country_code = order_info.get("country", "hk")
    country_flag = get_country_flag(country_code)

    users_col.update_one(
        {"user_id": uid},
        {"$inc": {"balance": -cost, "otp_count": 1}}
    )
    
    updated_user = get_user(uid)
    rem_bal = updated_user.get("balance", 0.0) if updated_user else 0.0
    set_number_status(id_num, "end")

    success_text = (
        f"✅ **𝙾𝚃𝙿 𝚁𝙴𝙲𝙴𝙸𝚅𝙴 𝚂𝚄𝙲𝙲𝙴𝚂𝚂𝙵𝚄𝚈!**\n\n"
        f"📱 **𝙽𝚄𝙼𝙱𝙴𝚁:** `<code>{phone}</code>`\n"
        f"🔑 **𝙾𝚃𝙿 𝙲𝙾𝙳𝙴:** `<code>{otp}</code>`\n\n"
        f"💵 **𝙱𝙰𝙻𝙰𝙽𝙲𝙴 𝙳𝙴𝙳𝙸𝙲𝙰𝚃𝙴𝙳:** `${cost}` USDT\n"
        f"💰 **𝚁𝙴𝙼𝙰𝙸𝙽𝙸𝙽𝙶 𝙱𝙰𝙻𝙰𝙽𝙲𝙴:** `${rem_bal:.4f}` USDT"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=uid,
            message_id=msg_id,
            text=success_text,
            parse_mode="HTML"
        )
    except Exception:
        await context.bot.send_message(chat_id=uid, text=success_text, parse_mode="HTML")

    masked_phone = mask_number(phone)
    group_forward_msg = (
        f"🌐 **COUNTRY:** `{country_code.upper()}` {country_flag}\n"
        f"📱 **𝙽𝚄𝙼𝙱𝙴𝚁:** `{masked_phone}`\n"
        f"🔑 **𝙾𝚃𝙿:** `{otp}`\n"
        f"💬 **Message:** `YOUR {service_type} CODE: {otp}`"
    )

    if OTP_GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=OTP_GROUP_ID,
                text=group_forward_msg,
                parse_mode="Markdown"
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
        [InlineKeyboardButton("🌸 𝙱𝙺𝙰𝚂𝙷 (50 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.40 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("❌ 𝙲𝙰𝙽𝙲𝙴𝙻", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 **𝙿𝙰𝚈𝙼𝙴𝙽𝚃 𝙼𝙴𝚃𝙷𝙾𝙳 𝚂𝙴𝙻𝙴𝙲𝚃 𝙺𝙾𝚁𝚄𝙽 (Plan: 5 Days - 50 Tk / 0.40 USDT):**", reply_markup=pay_methods_kb)
    return SUB_METHOD

async def sub_start_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    users_col.update_one({"user_id": user_id}, {"$set": {"sub_days": 7}})
    
    pay_methods_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 𝙱𝙺𝙰𝚂𝙷 (70 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.56 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("❌ 𝙲𝙰𝙽𝙲𝙴𝙻", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 **𝙿𝙰𝚈𝙼𝙴𝙽𝚃 𝙼𝙴𝚃𝙷𝙾𝙳 𝚂𝙴𝙻𝙴𝙲𝚃 𝙺𝙾𝚁𝚄𝙽 (Plan: 7 Days - 70 Tk / 0.56 USDT):**", reply_markup=pay_methods_kb)
    return SUB_METHOD

async def sub_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    user_doc = users_col.find_one({"user_id": user_id})
    days = user_doc.get("sub_days", 5) if user_doc else 5
    
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_flow_cb")]])
    
    if data == "pay_method_bkash":
        users_col.update_one({"user_id": user_id}, {"$set": {"sub_method": "bkash"}})
        price = 50 if days == 5 else 70
        msg = (
            f"💰 **𝙿𝙻𝙰𝙽:** `{days} Days`\n"
            f"💰 **𝙰𝙼𝙾𝚄𝙽𝚃:** `{price} Tk`\n\n"
            f"👇 **𝚂𝙴𝙽𝙳 𝙱𝙺𝙰𝚂𝙷 𝙿𝙴𝚁𝚂𝙾𝙽𝙰𝙻 𝙽𝚄𝙼𝙱𝙴𝚁:**\n"
            f"📱 𝙱𝙺𝙰𝚂𝙷 𝙽𝚄𝙼𝙱𝙴𝚁: `{ADMIN_BKASH}`\n\n"
            f"𝚃𝙰𝙺𝙰 𝙳𝙴𝙰 𝚂𝙴𝚂𝙴 **TrxID**-𝚃𝙸 𝙻𝙸𝙺𝙷𝙴 𝙿𝙰𝚃𝙷𝙰𝙽:"
        )
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_kb)
        return SUB_TXID
        
    elif data == "pay_method_binance":
        users_col.update_one({"user_id": user_id}, {"$set": {"sub_method": "binance"}})
        usdt_amt = 0.40 if days == 5 else 0.56
        msg = (
            f"💰 **𝙿𝙻𝙰𝙽:** `{days} Days`\n"
            f"💰 **𝙰𝙼𝙾𝚄𝙽𝚃:** `{usdt_amt}` USDT (Rate: 125 Tk/$)\n\n"
            f"👇 **𝚂𝙴𝙽𝙳 𝙱𝙸𝙽𝙰𝙽𝙲𝙴 𝙿𝙰𝚈 𝙸𝙳:**\n"
            f"🆔 𝙱𝙸𝙽𝙰𝙽𝙲𝙴 𝙿𝙰𝚈 𝙸𝙳: `{BINANCE_ID}`\n\n"
            f"ডলার সেন্ড করার পর আপনার **Order ID / TxID**-টি লিখে পাঠান:"
        )
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_kb)
        return SUB_TXID

async def sub_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["sub_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("📸 **𝙿𝙰𝚈𝙼𝙴𝙽𝚃 𝚂𝙲𝚁𝙴𝙴𝙽𝚂𝙷𝙾𝚃 𝙿𝙰𝚃𝙷𝙰𝙽:**", reply_markup=cancel_kb)
    return SUB_SCREENSHOT

async def sub_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    file_id = photo.file_id
    txid = context.user_data.get("sub_txid", "N/A")

    user_doc = users_col.find_one({"user_id": user.id})
    days = user_doc.get("sub_days", 5) if user_doc else 5
    method = user_doc.get("sub_method", "bkash") if user_doc else "bkash"
    
    if method == "bkash":
        price_str = f"{50 if days == 5 else 70} Tk"
    else:
        price_str = f"{0.40 if days == 5 else 0.56} USDT"

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve Sub", callback_data=f"approve_sub_{user.id}"),
            InlineKeyboardButton("❌ Reject Sub", callback_data=f"reject_sub_{user.id}")
        ]
    ])

    caption = (
        f"📩 **New Subscription Request!**\n\n"
        f"👤 **User:** {user.full_name} (`{user.id}`)\n"
        f"⏱ **Plan:** `{days} Days` ({price_str})\n"
        f"💳 **Method:** `{method.upper()}`\n"
        f"🔑 **TxID / Order ID:** `{txid}`"
    )

    try:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=admin_kb)
        await update.message.reply_text("✅ **Apnar Subscription request-ti Admin er kache pathano hoyeche.** Request approve hole apnake janiye dewa hobe.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error sending to Admin: {e}")

    return ConversationHandler.END

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        await update.message.reply_text("❌ Subscribed user-ra shudhu deposit korte parben.")
        return ConversationHandler.END

    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Deposit", callback_data="cancel_flow_cb")]])
    msg = (
        f"💵 **Deposit System (USDT Rate: 125 Tk / $1):**\n\n"
        f"📌 **Bkash Personal Number:** `{ADMIN_BKASH}`\n"
        f"📌 **Binance Pay ID:** `{BINANCE_ID}`\n\n"
        f"👉 Koto **USDT** Deposit korte chan ta likhe pathan (Dhorun: `2` ba `5`):"
    )
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=cancel_kb)
    return WAITING_AMOUNT

async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        amount = float(text)
        if amount <= 0:
            await update.message.reply_text("❌ Shothik amount likhun (e.g. 2, 5, 10).")
            return WAITING_AMOUNT
        context.user_data["dep_amount"] = amount
        bdt_val = amount * 125
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Deposit", callback_data="cancel_flow_cb")]])
        await update.message.reply_text(
            f"✅ Amount: `${amount}` USDT (`{bdt_val:.0f}` Tk)\n\n"
            f"Taka/Dollar Pathanor por apnar Payment **TrxID / Order ID**-ti Pathan:",
            reply_markup=cancel_kb
        )
        return WAITING_TXID
    except ValueError:
        await update.message.reply_text("❌ Kobi shongkhya (Number) likhun. (e.g. 2, 5, 10)")
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Deposit", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("📸 **Ebar Payment Screenshot-ti pathan:**", reply_markup=cancel_kb)
    return WAITING_SCREENSHOT

async def deposit_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    file_id = photo.file_id
    amount = context.user_data.get("dep_amount", 0.0)
    txid = context.user_data.get("dep_txid", "N/A")

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep_{user.id}_{amount}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep_{user.id}")
        ]
    ])

    caption = (
        f"📩 **New Deposit Request!**\n\n"
        f"👤 **User:** {user.full_name} (`{user.id}`)\n"
        f"💵 **Amount:** `${amount}` USDT\n"
        f"🔑 **TxID:** `{txid}`"
    )

    try:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption, parse_mode="Markdown", reply_markup=admin_kb)
        await update.message.reply_text("✅ **Apnar Deposit request-ti Admin er kache pathano hoyeche.** Verification seshe balance jukto hobe.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error sending to Admin: {e}")

    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❌ Prorkiya batil kora hoyeche.")
    else:
        await update.message.reply_text("❌ Prorkiya batil kora hoyeche.")
    return ConversationHandler.END

# Admin Flow Callbacks
async def admin_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🚫 **Enter Target User ID to Ban:**")
    return ADMIN_BAN

async def admin_ban_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"✅ User `{uid}` has been BANNED.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID!")
    return ConversationHandler.END

async def admin_unban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✅ **Enter Target User ID to Unban:**")
    return ADMIN_UNBAN

async def admin_unban_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
        await update.message.reply_text(f"✅ User `{uid}` has been UNBANNED.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID!")
    return ConversationHandler.END

async def admin_add_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("➕ **Enter Target User ID to Add Balance:**")
    return ADMIN_ADD_BAL_USER

async def admin_add_bal_user_rcvd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        context.user_data["target_add_bal_id"] = uid
        await update.message.reply_text(f"💵 **Enter Amount to Add for User `{uid}` (USDT):**", parse_mode="Markdown")
        return ADMIN_ADD_BAL_AMT
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID!")
        return ConversationHandler.END

async def admin_add_bal_amt_rcvd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.strip())
        uid = context.user_data.get("target_add_bal_id")
        users_col.update_one({"user_id": uid}, {"$inc": {"balance": amt}})
        await update.message.reply_text(f"✅ Successfully added `${amt}` USDT to User `{uid}`.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid Amount!")
    return ConversationHandler.END

async def admin_zero_bal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🔄 **Enter Target User ID to Reset Balance to $0:**")
    return ADMIN_ZERO_BAL_USER

async def admin_zero_bal_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"balance": 0.0}})
        await update.message.reply_text(f"✅ User `{uid}` balance reset to $0.00.", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID!")
    return ConversationHandler.END

async def admin_rate_wa_hk_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 **Enter NEW Rate for HK WhatsApp (e.g., 0.10):**")
    return ADMIN_RATE_WA_HK_SET

async def admin_rate_wa_hk_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("wa", "hk", rate)
        await update.message.reply_text(f"✅ HK WhatsApp rate set to `${rate}` USDT.")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate!")
    return ConversationHandler.END

async def admin_rate_wa_cl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 **Enter NEW Rate for Chile WhatsApp (e.g., 0.10):**")
    return ADMIN_RATE_WA_CL_SET

async def admin_rate_wa_cl_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("wa", "cl", rate)
        await update.message.reply_text(f"✅ Chile WhatsApp rate set to `${rate}` USDT.")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate!")
    return ConversationHandler.END

async def admin_rate_tg_hk_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 **Enter NEW Rate for HK Telegram (e.g., 0.12):**")
    return ADMIN_RATE_TG_HK_SET

async def admin_rate_tg_hk_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("tg", "hk", rate)
        await update.message.reply_text(f"✅ HK Telegram rate set to `${rate}` USDT.")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate!")
    return ConversationHandler.END

async def admin_rate_tg_cl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("💵 **Enter NEW Rate for Chile Telegram (e.g., 0.12):**")
    return ADMIN_RATE_TG_CL_SET

async def admin_rate_tg_cl_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        set_rate("tg", "cl", rate)
        await update.message.reply_text(f"✅ Chile Telegram rate set to `${rate}` USDT.")
    except ValueError:
        await update.message.reply_text("❌ Invalid Rate!")
    return ConversationHandler.END

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📢 **Enter Broadcast Message to Send to ALL Users:**")
    return ADMIN_BROADCAST

async def admin_broadcast_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    all_users = list(users_col.find({}))
    sent_cnt = 0
    for u in all_users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=f"📢 **BROADCAST ANNOUNCEMENT:**\n\n{msg}", parse_mode="Markdown")
            sent_cnt += 1
        except Exception:
            pass
    await update.message.reply_text(f"✅ Broadcast sent to {sent_cnt} users.")
    return ConversationHandler.END

# Corrected Bot Runner Function
def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers Setup
    sub_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(sub_start_5, pattern="^buy_sub_5$"),
            CallbackQueryHandler(sub_start_7, pattern="^buy_sub_7$")
        ],
        states={
            SUB_METHOD: [CallbackQueryHandler(sub_method_selected, pattern="^pay_method_")],
            SUB_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, sub_txid_received)],
            SUB_SCREENSHOT: [MessageHandler(filters.PHOTO, sub_screenshot_received)]
        },
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💵 𝙳𝙸𝙿𝙾𝚂𝙸𝚃$"), deposit_start)],
        states={
            WAITING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount_received)],
            WAITING_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_txid_received)],
            WAITING_SCREENSHOT: [MessageHandler(filters.PHOTO, deposit_screenshot_received)]
        },
        fallbacks=[CallbackQueryHandler(cancel_flow, pattern="^cancel_flow_cb$")]
    )

    admin_ban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_ban_start, pattern="^admin_ban_start$")],
        states={ADMIN_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_finish)]},
        fallbacks=[]
    )
    admin_unban_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_unban_start, pattern="^admin_unban_start$")],
        states={ADMIN_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_finish)]},
        fallbacks=[]
    )
    admin_add_bal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_bal_start, pattern="^admin_add_bal_start$")],
        states={
            ADMIN_ADD_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_user_rcvd)],
            ADMIN_ADD_BAL_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_bal_amt_rcvd)]
        },
        fallbacks=[]
    )
    admin_zero_bal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_zero_bal_start, pattern="^admin_zero_bal_start$")],
        states={ADMIN_ZERO_BAL_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_zero_bal_finish)]},
        fallbacks=[]
    )
    admin_rate_wa_hk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_wa_hk_start, pattern="^admin_rate_wa_hk_start$")],
        states={ADMIN_RATE_WA_HK_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_wa_hk_finish)]},
        fallbacks=[]
    )
    admin_rate_wa_cl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_wa_cl_start, pattern="^admin_rate_wa_cl_start$")],
        states={ADMIN_RATE_WA_CL_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_wa_cl_finish)]},
        fallbacks=[]
    )
    admin_rate_tg_hk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_tg_hk_start, pattern="^admin_rate_tg_hk_start$")],
        states={ADMIN_RATE_TG_HK_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_tg_hk_finish)]},
        fallbacks=[]
    )
    admin_rate_tg_cl_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_rate_tg_cl_start, pattern="^admin_rate_tg_cl_start$")],
        states={ADMIN_RATE_TG_CL_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_rate_tg_cl_finish)]},
        fallbacks=[]
    )
    admin_broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast_start$")],
        states={ADMIN_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_finish)]},
        fallbacks=[]
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

    logging.info("🤖 Bot polling started successfully.")
    app.run_polling(drop_pending_updates=True)

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    run_bot()

if __name__ == "__main__":
    main()
