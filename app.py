import logging
import os
import threading
import asyncio
import re
from datetime import datetime, timedelta, timezone
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

# Timezone Definition for Bangladesh (UTC+6)
BD_TIMEZONE = timezone(timedelta(hours=6))

def get_bd_time():
    return datetime.now(BD_TIMEZONE)

def parse_time_duration(time_str: str) -> timedelta:
    """
    Parse duration strings like:
    '5m' -> 5 minutes
    '5h' -> 5 hours
    '5d' -> 5 days
    '1d 12h 30m' -> combination
    '5' -> defaults to 5 days
    """
    clean_str = time_str.strip().lower()
    
    # If pure integer, treat as days
    if clean_str.isdigit():
        return timedelta(days=int(clean_str))
        
    days = 0
    hours = 0
    minutes = 0
    
    pattern = r'(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?'
    match = re.fullmatch(pattern, clean_str)
    
    if match and any(match.groups()):
        d, h, m = match.groups()
        if d: days = int(d)
        if h: hours = int(h)
        if m: minutes = int(m)
        return timedelta(days=days, hours=hours, minutes=minutes)
    
    return None

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
client = MongoClient(
    MONGODB_URI,
    maxPoolSize=50,
    wtimeoutMS=2500,
    connectTimeoutMS=5000,
    serverSelectionTimeoutMS=5000
)
db = client["vaksms_bot_db"]

users_col = db["users"]
settings_col = db["settings"]
otp_logs_col = db["otp_logs"]

# Helper Emoji Generator Function
def emoji(emoji_id: str, fallback: str = "✨") -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

# Specific Emoji Mapping
E_CL = emoji("5222350726340032308", "🇨🇱")
E_HK = emoji("5292166459118606932", "🇭🇰")
E_WA = emoji("5334759662677957452", "💬")
E_SUB_VALID = emoji("5352638632278660622", "📅")
E_WELCOME = emoji("5352838545826420397", "👋")
E_PHONE = emoji("5269759232483303288", "📱")
E_ID = emoji("5841276284155467413", "🆔")
E_WAIT_OTP = emoji("5215522595922779944", "⏳")
E_PAY_SELECT = emoji("5256186332669035163", "💳")
E_BINANCE = emoji("5348212415077064131", "💛")
E_CANCEL = emoji("5240241223632954241", "🚫")
E_PROFILE_HEADER = emoji("5352861489541714456", "👤")
E_NAME = emoji("5397916757333654639", "📛")
E_BAL = emoji("5409048419211682843", "💵")
E_OTP_RCV = emoji("5253742260054409879", "📩")
E_SUB_EXP = emoji("5413879192267805083", "📅")
E_SELECT_SERVICE = emoji("5231484622835063911", "📱")
E_ADMIN_HEADER = emoji("5424818078833715060", "🛠")
E_VIEW_USER = emoji("5451882707875276247", "👥")
E_STATS = emoji("5231200819986047254", "📊")
E_BAN = emoji("5240241223632954241", "🚫")
E_UNBAN = emoji("5206607081334906820", "✅")
E_ADD_BAL = emoji("5397916757333654639", "➕")
E_ZERO_BAL = emoji("5395695537687123235", "🔄")
E_BROADCAST = emoji("5424818078833715060", "📢")
E_GROUP_HEADER_1 = emoji("5447410659077661506", "🌐")
E_GROUP_HEADER_2 = emoji("5334759662677957452", "💬")
E_KEY_OTP = emoji("5282843764451195532", "🔑")

# Custom Requested Emojis
E_SELECT_COUNTRY_REQ = emoji("5397916757333654639", "🌐")
E_PANEL_BAL_REQ = emoji("5334530732331143967", "🏦")

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
    ADMIN_EXPIRY_USER,
    ADMIN_EXPIRY_TIME,
) = range(7, 19)

# Helper Functions
def get_country_flag(country_code: str) -> str:
    code = country_code.lower()
    if code == "hk":
        return E_HK
    elif code == "cl":
        return E_CL
    return emoji("5244837092042750681", "🌐")

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
            "selected_country": "cl",
            "selected_service": "wa",
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

def get_rate(service_code: str = "wa", country_code: str = "cl"):
    doc = settings_col.find_one({"type": "rates"})
    key = f"{service_code.lower()}_{country_code.lower()}"
    
    if doc and "rates" in doc and key in doc["rates"]:
        return float(doc["rates"][key])
    
    defaults = {
        "wa_hk": 0.09,
        "wa_cl": 0.09,
        "tg_hk": 0.12,
        "tg_cl": 0.12
    }
    return defaults.get(key, 0.09)

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
        if get_bd_time().replace(tzinfo=None) < expiry:
            return True
    return False

# Keyboards
def get_main_keyboard(user_id):
    keyboard = [
        [KeyboardButton("💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴"), KeyboardButton("🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁")],
        [KeyboardButton("🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂"), KeyboardButton("📱 𝚂𝙴𝚃 𝚂𝙴𝚁𝚅𝙸𝙲𝙴")],
        [KeyboardButton("👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴"), KeyboardButton("💵 𝙳𝙴𝙿𝙾𝚂𝙸𝚃")]
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

def buy_vak_number(service: str = "wa", country: str = "cl", max_price: float = 0.087):
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
        await update.message.reply_text(f"{E_BAN} BANNED BY ADMIN. PLEASE CONTACT ADMIN.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 BOT UNDER MAINTENANCE BY ADMIN. Please try again later.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        msg = (
            f"👋 Hello {user.full_name}!\n\n"
            f"{E_BAN} <b>YOU DO NOT HAVE AN ACTIVE SUBSCRIPTION FOR THE BOT!</b>\n"
            f"Bot bebohar korte chaile subscription nite hobe.\n\n"
            f"📌 PRICE & VALIDITY (Rate: 125 Tk/$) :\n"
            f"• 5 Days = 50 Tk (or 0.40 USDT)\n"
            f"• 7 Days = 70 Tk (or 0.56 USDT)\n\n"
            f"Nicher button-e click kore subscription kinun:"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    exp_time = u_data.get("subscription_expiry")
    if user_id == ADMIN_ID:
        exp_str = "Unlimited (Admin)"
    elif exp_time:
        exp_str = exp_time.strftime("%Y-%m-%d %H:%M")
    else:
        exp_str = "N/A"

    curr_country_code = u_data.get("selected_country", "cl")
    curr_country = curr_country_code.upper()
    country_flag = get_country_flag(curr_country_code)
    curr_service_code = u_data.get("selected_service", "wa").lower()
    service_icon = E_WA if curr_service_code == "wa" else emoji("5289967092265660622", "✈️")
    curr_service = curr_service_code.upper()

    welcome_msg = (
        f"👋 WELCOME TO REX PRIVATE BOT! {E_WELCOME}\n\n"
        f"⚙️ RECENT SETUP:\n"
        f"• COUNTRIES: {curr_country} {country_flag}\n"
        f"• Service: {curr_service} {service_icon}\n"
        f"• YOUR BALANCE: <code>{u_data.get('balance', 0.0):.4f}</code> USDT {E_BAL}\n"
        f"• SUBSCRIPTION VALID TILL: {exp_str} {E_SUB_VALID}\n\n"
        f"Kaj korte niche dewa menu use korun:"
    )
    await update.message.reply_text(welcome_msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text(f"{E_BAN} YOUR ACCOUNT HAS BEEN BANNED BY ADMIN.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("🚧 BOT UNDER MAINTENANCE BY ADMIN. Try again after some time.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("💳 7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        await update.message.reply_text(f"{E_BAN} SUBSCRIPTION EXPIRED! BUY NEW SUBSCRIPTION.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("👇 BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    text = update.message.text.strip()

    if text == "💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"<b>MY BALANCE:</b> <code>{bot_bal:.4f}</code> USDT {E_BAL}"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\nPANEL BALANCE : <code>{site_bal:.4f}</code> USD\n{E_PANEL_BAL_REQ}"
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    if text in ["👤 𝙼𝚈 𝙿𝙾𝚁𝙵𝙸𝙻𝙴", "👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴"]:
        bot_bal = u_data.get("balance", 0.0)
        otp_cnt = u_data.get("otp_count", 0)
        exp_time = u_data.get("subscription_expiry")
        if user_id == ADMIN_ID:
            exp_str = "Unlimited (Admin)"
        elif exp_time:
            exp_str = exp_time.strftime("%Y-%m-%d %H:%M")
        else:
            exp_str = "N/A"
        
        profile_msg = (
            f"Apnar Profile Info: {E_PROFILE_HEADER}\n"
            f"User ID: <code>{user_id}</code> {E_ID}\n"
            f"Name: {user.full_name} {E_NAME}\n"
            f"Balance: <code>{bot_bal:.4f}</code> USDT {E_BAL}\n"
            f"Total OTP Received: <code>{otp_cnt}</code> {E_OTP_RCV}\n"
            f"Subscription Valid: {exp_str} {E_SUB_EXP}"
        )
        await update.message.reply_text(profile_msg, parse_mode="HTML")
        return

    # COUNTRY SELECTION
    if text in ["🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝚈", "🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂"]:
        country_kb = [
            [KeyboardButton("COUNTRY: HK 🇭🇰 (HONG KONG)"), KeyboardButton("COUNTRY: CHILE 🇨🇱 (CL)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text(f"🌐 SELECT YOUR COUNTRY:\n{E_SELECT_COUNTRY_REQ}", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if "HK" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "hk"}})
        await update.message.reply_text(f"Country set: HONG KONG (HK) {E_HK} {E_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "CHILE" in text or "CL" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "cl"}})
        await update.message.reply_text(f"Country set: CHILE (CL) {E_CL} {E_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    # SERVICE SELECTION
    if text == "📱 𝚂𝙴𝚃 𝚂𝙴器件":
        service_kb = [
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲E: TG (𝚃𝙴𝙻𝙴𝙶𝚁𝙰𝙼)")],
            [KeyboardButton("𝚂𝙴𝚁𝚅𝙸𝙲𝙴: WA (𝚆𝙷𝙰𝚃𝚂𝙰𝙿𝙿)")],
            [KeyboardButton("🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄")]
        ]
        await update.message.reply_text(f"📱 SELECT YOUR SERVICE:\n{E_SELECT_SERVICE}", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if "TG" in text or "TELEGRAM" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "tg"}})
        await update.message.reply_text(f"SERVICE SET: TELEGRAM (TG) {E_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "WA" in text or "WHATSAPP" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "wa"}})
        await update.message.reply_text(f"SERVICE SET: WHATSAPP (WA) {E_WA} {E_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if text == "🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄":
        await start(update, context)
        return

    if text == "🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁":
        user_has_active = any(order.get("user_id") == user_id for order in active_orders.values())
        if user_has_active:
            await update.message.reply_text(
                "⚠️ <b>Allready ekta number kena royeche!</b>\nNotun number kenar age ager number-ti bebohar somponno korun othoba Cancel korun.",
                parse_mode="HTML"
            )
            return

        country = u_data.get("selected_country", "cl")
        service = u_data.get("selected_service", "wa")
        country_flag = get_country_flag(country)
        service_icon = E_WA if service == "wa" else emoji("5289967092265660622", "✈️")
        
        bot_rate = get_rate(service_code=service, country_code=country)
        user_bal = u_data.get("balance", 0.0)

        if user_bal < bot_rate:
            await update.message.reply_text(
                f"{E_BAN} <b>SORRY NOT ENOUGH BALANCE:</b> <code>{bot_rate}</code> USDT, YOUR BALANCE: <code>{user_bal:.4f}</code> USDT.\nPLEASE DEPOSIT.",
                parse_mode="HTML"
            )
            return

        status_msg = await update.message.reply_text(
            f"⏳ {country.upper()} {country_flag} BUYING NUMBER... WAIT A FEW SECONDS.",
            parse_mode="HTML"
        )

        res = buy_vak_number(service=service, country=country, max_price=0.087)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            raw_phone = str(res["tel"])
            phone_num = f"+{raw_phone}" if not raw_phone.startswith("+") else raw_phone
            id_num = str(res["idNum"])

            inline_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Check Active OTP", callback_data=f"check_otp_{id_num}")],
                [InlineKeyboardButton("Cancel Number", callback_data=f"cancel_num_{id_num}")]
            ])

            sent_msg = await update.message.reply_text(
                f"NUMBER PURCHASED SUCCESSFULLY! {E_UNBAN}\n\n"
                f"Number: <code>{phone_num}</code> {E_PHONE}\n"
                f"ID Num: <code>{id_num}</code> {E_ID}\n"
                f"Country: {country.upper()} {country_flag}\n"
                f"Service: {service.upper()} {service_icon}\n"
                f"Rate: <code>{bot_rate}</code> USDT {E_BAL} (OTP ASLEI BALANCE KATBE)\n\n"
                f"OTP POWAR JONNO OPEK KHA KORUN... {E_WAIT_OTP}",
                parse_mode="HTML",
                reply_markup=inline_kb
            )

            active_orders[id_num] = {
                "user_id": user_id,
                "service": service,
                "country": country,
                "cost": bot_rate,
                "phone": phone_num,
                "msg_id": sent_msg.message_id,
                "user_msg_id": update.message.message_id
            }

            try:
                await status_msg.delete()
            except Exception:
                pass

            asyncio.create_task(auto_check_otp(context, user_id, id_num, str(phone_num), sent_msg.message_id))
        else:
            err_msg = res.get("error", "Stock Out!") if isinstance(res, dict) else "Stock Out!"
            await update.message.reply_text(f"{E_BAN} {err_msg}", parse_mode="HTML")
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
        [InlineKeyboardButton("📅 SET USER EXPIRY", callback_data="admin_set_expiry_start")],
        [InlineKeyboardButton("📢 BROADCAST ALL", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(f"BOT STATUS: {status_str}", callback_data="admin_toggle_bot")]
    ])
    admin_text = f"{E_ADMIN_HEADER}"
    if update.message:
        await update.message.reply_text(admin_text, parse_mode="HTML", reply_markup=admin_kb)
    elif update.callback_query:
        await update.callback_query.message.reply_text(admin_text, parse_mode="HTML", reply_markup=admin_kb)

async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "admin_view_users" and user_id == ADMIN_ID:
        try:
            now = get_bd_time().replace(tzinfo=None)
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
                exp = u.get("subscription_expiry")
                exp_s = exp.strftime("%Y-%m-%d %H:%M") if exp else "N/A"
                msg += f"• {safe_name} (<code>{uid}</code>)\n  └ 💰 Balance: <code>{bal:.4f}</code> USDT | 📩 OTP Rcv: <code>{otp_cnt}</code>\n  └ 📅 Valid Till: <code>{exp_s}</code>\n\n"
                
            await query.message.reply_text(msg, parse_mode="HTML")
        except Exception as e:
            await query.message.reply_text(f"❌ Error loading users: {str(e)}")

    elif data == "admin_daily_stats" and user_id == ADMIN_ID:
        try:
            all_dates = otp_logs_col.distinct("date")
            today_str = get_bd_time().strftime("%Y-%m-%d")
            
            if today_str not in all_dates:
                all_dates.append(today_str)

            all_dates = sorted(all_dates, reverse=True)

            if not all_dates:
                await query.message.reply_text("📊 Kono OTP log ekhono nei.")
                return

            dates_kb = []
            for d in all_dates[:10]:
                label = f"📅 Date: {d} {'(Today)' if d == today_str else ''}"
                dates_kb.append([InlineKeyboardButton(label, callback_data=f"view_stats_{d}")])

            await query.message.reply_text(
                "📊 DAILY OTP STATS ARCHIVE\n\n"
                "Nicher tarikh (date) gulo theke click kore jekono diner report dekhte paren:",
                reply_markup=InlineKeyboardMarkup(dates_kb)
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Error generating stats: {str(e)}")

    elif data.startswith("view_stats_") and user_id == ADMIN_ID:
        target_date = data.split("_")[2]
        try:
            logs = list(otp_logs_col.find({"date": target_date}))
            
            total_count = len(logs)
            hk_count = sum(1 for log in logs if str(log.get("country", "")).lower() == "hk")
            cl_count = sum(1 for log in logs if str(log.get("country", "")).lower() == "cl")
            other_count = total_count - (hk_count + cl_count)
            
            wa_count = sum(1 for log in logs if str(log.get("service", "")).lower() == "wa")
            tg_count = sum(1 for log in logs if str(log.get("service", "")).lower() == "tg")

            stats_msg = (
                f"📊 OTP STATS FOR <code>{target_date}</code>:\n\n"
                f"📱 Total OTP Received: <code>{total_count}</code>\n"
                f"────────────────────\n"
                f"🇭🇰 Hong Kong (HK): <code>{hk_count}</code>\n"
                f"🇨🇱 Chile (CL): <code>{cl_count}</code>\n"
                f"🌐 Others: <code>{other_count}</code>\n"
                f"────────────────────\n"
                f"💬 WhatsApp (WA): <code>{wa_count}</code>\n"
                f"✈️ Telegram (TG): <code>{tg_count}</code>"
            )
            await query.message.reply_text(stats_msg, parse_mode="HTML")
        except Exception as e:
            await query.message.reply_text(f"❌ Error loading stats for {target_date}: {str(e)}")

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
            [InlineKeyboardButton("📅 SET USER EXPIRY", callback_data="admin_set_expiry_start")],
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
            order_data = active_orders.pop(id_num, None)
            
            try:
                await query.message.delete()
            except Exception:
                pass
            
            if order_data and "user_msg_id" in order_data:
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=order_data["user_msg_id"])
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
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{E_UNBAN} Approved & Balance Added!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 Apnar <code>{amount}</code> USDT deposit shofolbhabe jukto kora hoyeche!", parse_mode="HTML")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{E_BAN} Deposit Rejected!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        user_doc = users_col.find_one({"user_id": target_id})
        days = user_doc.get("sub_days", 5) if user_doc else 5
        expiry_date = get_bd_time().replace(tzinfo=None) + timedelta(days=days)
        users_col.update_one({"user_id": target_id}, {"$set": {"subscription_expiry": expiry_date}})
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{E_UNBAN} Subscription Approved ({days} Days Active)!", parse_mode="HTML")
        
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🎉 Apnar Subscription Approved hoyeche! {days} Diner jonno bot-er sob features active kora hoyeche.",
            reply_markup=get_main_keyboard(target_id)
        )

    elif data.startswith("reject_sub_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{E_BAN} Subscription Rejected!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar subscription request-ti batil kora hoyeche.")

async def process_otp_success(context, id_num: str, otp: str):
    if id_num not in active_orders:
        return

    order_info = active_orders.pop(id_num)
    uid = order_info["user_id"]
    cost = order_info["cost"]
    phone = order_info["phone"]
    msg_id = order_info["msg_id"]
    service_type = order_info.get("service", "wa").upper()
    country_code = order_info.get("country", "cl")
    country_flag = get_country_flag(country_code)

    users_col.update_one(
        {"user_id": uid},
        {"$inc": {"balance": -cost, "otp_count": 1}}
    )

    try:
        bd_now = get_bd_time()
        otp_logs_col.insert_one({
            "user_id": uid,
            "country": country_code,
            "service": service_type,
            "date": bd_now.strftime("%Y-%m-%d"),
            "timestamp": bd_now
        })
    except Exception as e:
        logging.error(f"Failed to log OTP: {e}")
    
    updated_user = get_user(uid)
    rem_bal = updated_user.get("balance", 0.0) if updated_user else 0.0
    set_number_status(id_num, "end")

    success_text = (
        f"OTP RECEIVE SUCCESSFUL! {E_UNBAN}\n\n"
        f"Number: <code>{phone}</code> {E_PHONE}\n"
        f"OTP Code: <code>{otp}</code> {E_KEY_OTP}\n\n"
        f"Balance Deducted: <code>{cost}</code> USDT {E_BAL}\n"
        f"Remaining Balance: <code>{rem_bal:.4f}</code> USDT"
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
    
    # Conditional formatting for group notification
    if country_code.lower() == "hk":
        group_forward_msg = (
            f"🌐 COUNTRY: HK {E_HK}\n"
            f"📱 NUMBER: <code>{masked_phone}</code>\n"
            f"🔑 OTP: <code>{otp}</code>\n"
            f"💬 Message: YOUR {service_type} CODE: <code>{otp}</code>"
        )
    else:
        group_forward_msg = (
            f"COUNTRY: CL {E_CL}\n"
            f"NUMBER: <code>{masked_phone}</code> {E_PHONE}\n"
            f"OTP: <code>{otp}</code> {E_KEY_OTP}\n"
            f"Message: YOUR {service_type} CODE: <code>{otp}</code> {E_WA}"
        )

    if OTP_GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=OTP_GROUP_ID,
                text=group_forward_msg,
                parse_mode="HTML"
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

# Subscription Purchase Flow Handlers
async def sub_start_5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    users_col.update_one({"user_id": user_id}, {"$set": {"sub_days": 5}})
    
    pay_methods_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 bKash (50 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.40 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 Payment Method select korun (Plan: 5 Days - 50 Tk / 0.40 USDT):", reply_markup=pay_methods_kb)
    return SUB_METHOD

async def sub_start_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    users_col.update_one({"user_id": user_id}, {"$set": {"sub_days": 7}})
    
    pay_methods_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌸 bKash (70 Tk)", callback_data="pay_method_bkash")],
        [InlineKeyboardButton("💛 Binance Pay (0.56 USDT)", callback_data="pay_method_binance")],
        [InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]
    ])
    await query.message.reply_text("💳 Payment Method select korun (Plan: 7 Days - 70 Tk / 0.56 USDT):", reply_markup=pay_methods_kb)
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
            f"💰 Plan: <code>{days} Days</code>\n"
            f"💰 Amount: <code>{price} Tk</code>\n\n"
            f"👇 Send bKash Personal Number:\n"
            f"📱 bKash Number: <code>{ADMIN_BKASH}</code>\n\n"
            f"Taka dewa sese **TrxID**-ti likhe pathan:"
        )
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=cancel_kb)
        return SUB_TXID
        
    elif data == "pay_method_binance":
        users_col.update_one({"user_id": user_id}, {"$set": {"sub_method": "binance"}})
        usdt_amt = 0.40 if days == 5 else 0.56
        msg = (
            f"💰 Plan: <code>{days} Days</code>\n"
            f"💰 Amount: <code>{usdt_amt}</code> USDT (Rate: 125 Tk/$)\n\n"
            f"👇 Send Binance Pay ID:\n"
            f"🆔 Binance Pay ID: <code>{BINANCE_ID}</code>\n\n"
            f"Dollar pathanor por apnar **Order ID / TxID**-ti লিখে পাঠান:"
        )
        await query.message.reply_text(msg, parse_mode="HTML", reply_markup=cancel_kb)
        return SUB_TXID

async def sub_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["sub_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("📸 Ekhon apnar payment-er screenshot (Photo) Pathan:", reply_markup=cancel_kb)
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
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{user.id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{user.id}")
        ]
    ])

    caption = (
        f"🔔 Notun Subscription Request!\n\n"
        f"👤 User: {user.full_name} (<code>{user.id}</code>)\n"
        f"💳 Method: <code>{method}</code>\n"
        f"📅 Plan: <code>{days} Days</code>\n"
        f"💰 Amount: <code>{price_str}</code>\n"
        f"🧾 TrxID / Order ID: <code>{txid}</code>"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="HTML", reply_markup=admin_kb)
    await update.message.reply_text("✅ Apnar subscription request admin-er kache pathano hoyeche! Admin approve korlei bot active hoye jaabe.")
    return ConversationHandler.END

# Admin Panel Conversation Handlers
async def admin_ban_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🚫 Banned korte chawa User ID-ti likhe pathan:")
    return ADMIN_BAN

async def admin_ban_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        users_col.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
        await update.message.reply_text(f"✅ User <code>{uid}</code>-ke banned kora hoyeche.", parse_mode="HTML")
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
        await update.message.reply_text(f"✅ User <code>{uid}</code>-ke unban kora hoyeche.", parse_mode="HTML")
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
        await update.message.reply_text(f"💰 User <code>{uid}</code>-er jonno koto USDT balance add korben ta likhun:", parse_mode="HTML")
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
        
        await update.message.reply_text(f"✅ Successfully added <code>{amt}</code> USDT to User <code>{uid}</code>. Notun Balance: <code>{new_bal:.4f}</code> USDT", parse_mode="HTML")
        await context.bot.send_message(chat_id=uid, text=f"🎉 Admin apnar account-e <code>{amt}</code> USDT balance add koreche!", parse_mode="HTML")
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
            await update.message.reply_text(f"✅ Successfully User <code>{uid}</code>-er balance <b>0 USDT</b> kora hoyeche.", parse_mode="HTML")
            try:
                await context.bot.send_message(chat_id=uid, text="⚠️ Admin apnar account-er balance 0 kore diyeche.")
            except Exception:
                pass
        else:
            await update.message.reply_text(f"❌ Database-e <code>{uid}</code> ID-er kono user pawa jayni.", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID! Sothik shongkha likhun.")
    return ConversationHandler.END

async def admin_set_expiry_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📅 Subscription validity set/reset korte chawa **User ID**-ti likhe pathan:")
    return ADMIN_EXPIRY_USER

async def admin_set_expiry_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
        user = get_user(uid)
        if not user:
            await update.message.reply_text(f"❌ User ID <code>{uid}</code> database-e pawa jayni.", parse_mode="HTML")
            return ConversationHandler.END
        context.user_data["target_exp_uid"] = uid
        
        msg_text = (
            f"📅 User <code>{uid}</code>-er jonno active duration-ti likhun:\n\n"
            f"📌 Example Formats:\n"
            f"• <code>5m</code> = 5 Minutes\n"
            f"• <code>5h</code> = 5 Hours\n"
            f"• <code>5d</code> = 5 Days\n"
            f"• <code>1d 12h</code> = 1 Day 12 Hours\n"
            f"• <code>5</code> = 5 Days (Default)"
        )
        await update.message.reply_text(msg_text, parse_mode="HTML")
        return ADMIN_EXPIRY_TIME
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID! Sothik shongkha likhun.")
        return ConversationHandler.END

async def admin_set_expiry_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    input_str = update.message.text.strip()
    uid = context.user_data.get("target_exp_uid")
    
    delta = parse_time_duration(input_str)
    
    if not delta or delta.total_seconds() <= 0:
        await update.message.reply_text("❌ Invalid Format! Doya kore '5m', '5h', '5d' ba '1d 12h' erom likhe pathan.")
        return ConversationHandler.END

    expiry_date = get_bd_time().replace(tzinfo=None) + delta
    users_col.update_one({"user_id": uid}, {"$set": {"subscription_expiry": expiry_date}})
    
    exp_str = expiry_date.strftime("%Y-%m-%d %H:%M")
    await update.message.reply_text(
        f"✅ User <code>{uid}</code>-er subscription <b>{input_str}</b>-er jonno active kora hoyeche.\n"
        f"📅 New Expiry: <code>{exp_str}</code> (BD Time)",
        parse_mode="HTML"
    )
    try:
        await context.bot.send_message(
            chat_id=uid,
            text=f"🎉 Admin apnar subscription validity **{input_str}** set kore diyeche!\n📅 Valid till: <code>{exp_str}</code>",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(uid)
        )
    except Exception:
        pass
        
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
        await update.message.reply_text(f"✅ Hong Kong WA Rate update kora hoyeche: <code>{rate}</code> USDT", parse_mode="HTML")
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
        await update.message.reply_text(f"✅ Chile WA Rate update kora hoyeche: <code>{rate}</code> USDT", parse_mode="HTML")
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
        await update.message.reply_text(f"✅ Hong Kong TG Rate update kora hoyeche: <code>{rate}</code> USDT", parse_mode="HTML")
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
        await update.message.reply_text(f"✅ Chile TG Rate update kora hoyeche: <code>{rate}</code> USDT", parse_mode="HTML")
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
        f"✅ Success: <code>{success_count}</code> Users\n"
        f"❌ Failed/Blocked: <code>{fail_count}</code> Users"
    )
    await status_msg.edit_text(result_text, parse_mode="HTML")
    return ConversationHandler.END

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💛 Binance Pay", callback_data="pay_binance")],
        [InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]
    ])
    msg = (
        f"Payment Method select korun: {E_PAY_SELECT}\n"
        f"Binance pay {E_BINANCE}"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def deposit_binance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    
    msg_select = (
        f"📥 Apni koto USDT pathaben ta likhe janan (Minimum: 1 USDT, jemon: 1, 2.5, 5):\n"
        f"{emoji('5395444784611480792', '📥')}"
    )
    await query.message.reply_text(msg_select, parse_mode="HTML", reply_markup=cancel_kb)
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
            f"💰 Deposit Amount: <code>{amount}</code> USDT {emoji('5224257782013769471', '💰')}\n\n"
            f"👇 Nicher Binance Pay ID-te Binance app theke Pay/Send Money Korun:\n"
            f"Binance Pay ID: <code>{BINANCE_ID}</code> {E_BINANCE}\n\n"
            f"⚠️ Note: Minimum deposit 1 USDT. Binance Pay-er madhyome kono extra fee charai pathano jabe.\n"
            f"{emoji('5420323339723881652', '⚠️')}\n\n"
            f"Dollar pathanor por apnar Order ID / TxID-ti likhe message din:\n"
            f"{emoji('5217822164362739968', '🧾')}"
        )
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=cancel_kb)
        return WAITING_TXID
    except ValueError:
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text("❌ Sothik shongkha likhun (jemon: 1 ba 5).", reply_markup=cancel_kb)
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Cancel", callback_data="cancel_flow_cb")]])
    
    msg_photo = (
        f"📸 Ekhon apnar payment-er screenshot (Photo) Pathan:\n"
        f"{emoji('5895427227528467580', '📸')}"
    )
    await update.message.reply_text(msg_photo, parse_mode="HTML", reply_markup=cancel_kb)
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
        f"📥 Notun Deposit Request! {emoji('5445355530111437729', '📥')}\n\n"
        f"👤 User: {user.full_name} (<code>{user.id}</code>) {emoji('5193063022226086560', '👤')}\n"
        f"💰 Amount: <code>{amount}</code> USDT {emoji('5224257782013769471', '💰')}\n"
        f"🧾 TxID: <code>{txid}</code> {emoji('5271604874419647061', '🧾')}"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="HTML", reply_markup=admin_kb)
    await update.message.reply_text(f"{E_UNBAN} Apnar deposit request admin-er kache pathano hoyeche! Jaachai kore druto balance jukto kora hobe.", parse_mode="HTML")
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

# Async Main Runner with All Admin, Subscription & Deposit Handlers
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
        entry_points=[MessageHandler(filters.Regex("^💵 𝙳𝙴𝙿𝙾𝚂𝙸𝚃$"), deposit_start)],
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

    admin_set_expiry_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_expiry_start, pattern="^admin_set_expiry_start$")],
        states={
            ADMIN_EXPIRY_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_expiry_user)],
            ADMIN_EXPIRY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_expiry_days)]
        },
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
    app.add_handler(admin_set_expiry_conv)
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
