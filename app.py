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

# Helper Function for Premium Custom Emoji
def prem_emoji(emoji_id: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}"></tg-emoji>'

# Premium Custom Emoji Mapping
P_CL = prem_emoji("5222350726340032308")
P_HK = prem_emoji("5269657987219232606")
P_WA = prem_emoji("5334759662677957452")
P_TG = prem_emoji("5289967092265660622")
P_SUB_VALID = prem_emoji("5352638632278660622")
P_WELCOME = prem_emoji("5352838545826420397")
P_PHONE = prem_emoji("5269759232483303288")
P_ID = prem_emoji("5841276284155467413")
P_WAIT_OTP = prem_emoji("5215522595922779944")
P_PAY_SELECT = prem_emoji("5256186332669035163")
P_BINANCE = prem_emoji("5348212415077064131")
P_BKASH = prem_emoji("5379947378671395461")
P_CANCEL = prem_emoji("5240241223632954241")
P_PROFILE_HEADER = prem_emoji("5352861489541714456")
P_NAME = prem_emoji("5397916757333654639")
P_BAL = prem_emoji("5409048419211682843")
P_OTP_RCV = prem_emoji("5253742260054409879")
P_SUB_EXP = prem_emoji("5413879192267805083")
P_SELECT_SERVICE = prem_emoji("5231484622835063911")
P_ADMIN_HEADER = prem_emoji("5424818078833715060")
P_VIEW_USER = prem_emoji("5451882707875276247")
P_STATS = prem_emoji("5231200819986047254")
P_BAN = prem_emoji("5240241223632954241")
P_UNBAN = prem_emoji("5206607081334906820")
P_ADD_BAL = prem_emoji("5397916757333654639")
P_ZERO_BAL = prem_emoji("5395695537687123235")
P_BROADCAST = prem_emoji("5424818078833715060")
P_GROUP_HEADER_1 = prem_emoji("5447410659077661506")
P_KEY_OTP = prem_emoji("5282843764451195532")
P_GLOBE = prem_emoji("5244837092042750681")

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
        return P_HK
    elif code == "cl":
        return P_CL
    return P_GLOBE

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
        await update.message.reply_text(f"BANNED BY ADMIN. {P_BAN}", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("BOT UNDER MAINTENANCE BY ADMIN.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        msg = (
            f"Hello {user.full_name}!\n\n"
            f"<b>YOU DO NOT HAVE AN ACTIVE SUBSCRIPTION!</b> {P_BAN}\n"
            f"Bot bebohar korte chaile subscription nite hobe."
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    exp_time = u_data.get("subscription_expiry")
    exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"

    curr_country_code = u_data.get("selected_country", "cl")
    curr_country = curr_country_code.upper()
    country_flag = get_country_flag(curr_country_code)
    curr_service_code = u_data.get("selected_service", "wa").lower()
    service_icon = P_WA if curr_service_code == "wa" else P_TG
    curr_service = curr_service_code.upper()

    welcome_msg = (
        f"WELCOME TO REX PRIVATE BOT! {P_WELCOME}\n\n"
        f"RECENT SETUP:\n"
        f"• COUNTRIES: {curr_country} {country_flag}\n"
        f"• Service: {curr_service} {service_icon}\n"
        f"• YOUR BALANCE: {u_data.get('balance', 0.0):.4f} USDT {P_BAL}\n"
        f"• SUBSCRIPTION VALID TILL: Unlimited (Admin) {P_SUB_VALID}\n\n"
        f"Kaj korte niche dewa menu use korun: {P_WELCOME}"
    )
    await update.message.reply_text(welcome_msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text(f"YOUR ACCOUNT HAS BEEN BANNED. {P_BAN}", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("BOT UNDER MAINTENANCE.")
        return

    if not is_subscribed(user_id):
        return

    text = update.message.text.strip()

    if text == "💳 𝙰𝙲𝙲𝙾𝚄𝙽𝚃 𝙱𝙰𝙻𝙰𝙽𝙲𝙴":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"<b>MY BALANCE:</b> {bot_bal:.4f} USDT {P_BAL}"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n<b>PANEL BALANCE:</b> {site_bal:.4f} USD"
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    if text in ["👤 𝙼𝚈 𝙿𝙾𝚁𝙵𝙸𝙻𝙴", "👤 𝙼𝚈 𝙿𝚁𝙾𝙵𝙸𝙻𝙴", "MY PROFILE", "MY PORFILE"]:
        bot_bal = u_data.get("balance", 0.0)
        otp_cnt = u_data.get("otp_count", 0)
        
        profile_msg = (
            f"Apnar Profile Info: {P_PROFILE_HEADER}\n"
            f"User ID: {user_id} {P_ID}\n"
            f"Name: {user.full_name} {P_NAME}\n"
            f"Balance: {bot_bal:.4f} USDT {P_BAL}\n"
            f"Total OTP Received: {otp_cnt} {P_OTP_RCV}\n"
            f"Subscription Valid: Unlimited (Admin) {P_SUB_EXP}"
        )
        await update.message.reply_text(profile_msg, parse_mode="HTML")
        return

    # COUNTRY SELECTION
    if text in ["🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝚈", "🌐 𝚂𝙴𝚃 𝙲𝙾𝚄𝙽𝚃𝚁𝙸𝙴𝚂", "SET COUNTRY", "SET COUNTRIES"]:
        country_kb = [
            [KeyboardButton("COUNTRY: HK (HONG KONG)"), KeyboardButton("COUNTRY: CHILE (CL)")],
            [KeyboardButton("MAIN MENU")]
        ]
        await update.message.reply_text(f"SELECT YOUR COUNTRY: {P_GLOBE}", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if "HK" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "hk"}})
        await update.message.reply_text(f"Country set: HONG KONG (HK) {P_HK} {P_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "CHILE" in text or "CL" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "cl"}})
        await update.message.reply_text(f"Country set: CHILE (CL) {P_CL} {P_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    # SERVICE SELECTION
    if text in ["📱 𝚂𝙴𝚃 𝚂𝙴器件", "SET SERVICE"]:
        service_kb = [
            [KeyboardButton("SERVICE: TG (TELEGRAM)")],
            [KeyboardButton("SERVICE: WA (WHATSAPP)")],
            [KeyboardButton("MAIN MENU")]
        ]
        await update.message.reply_text(f"SELECT YOUR SERVICE: {P_SELECT_SERVICE}", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if "TG" in text or "TELEGRAM" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "tg"}})
        await update.message.reply_text(f"SERVICE SET: TELEGRAM (TG) {P_TG} {P_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "WA" in text or "WHATSAPP" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "wa"}})
        await update.message.reply_text(f"SERVICE SET: WHATSAPP (WA) {P_WA} {P_UNBAN}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if text in ["🔙 𝙼𝙰𝙸𝙽 𝙼𝙴𝙽𝚄", "MAIN MENU"]:
        await start(update, context)
        return

    if text in ["🛒 𝙱𝚈 𝙽𝚄𝙼𝙱𝙴𝚁", "BY NUMBER"]:
        user_has_active = any(order.get("user_id") == user_id for order in active_orders.values())
        if user_has_active:
            await update.message.reply_text(
                f"<b>Already ekti number active ache!</b> {P_BAN}",
                parse_mode="HTML"
            )
            return

        country = u_data.get("selected_country", "cl")
        service = u_data.get("selected_service", "wa")
        country_flag = get_country_flag(country)
        service_icon = P_WA if service == "wa" else P_TG
        
        bot_rate = get_rate(service_code=service, country_code=country)
        user_bal = u_data.get("balance", 0.0)

        if user_bal < bot_rate:
            await update.message.reply_text(
                f"<b>NOT ENOUGH BALANCE:</b> {bot_rate} USDT {P_BAN}",
                parse_mode="HTML"
            )
            return

        status_msg = await update.message.reply_text(
            f"{country.upper()} {country_flag} BUYING NUMBER...",
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
                f"NUMBER PURCHASED SUCCESSFULLY! {P_UNBAN}\n\n"
                f"Number: {phone_num} {P_PHONE}\n"
                f"ID Num: {id_num} {P_ID}\n"
                f"Country: {country.upper()} {country_flag}\n"
                f"Service: {service.upper()} {service_icon}\n"
                f"Rate: {bot_rate} USDT {P_BAL}\n\n"
                f"OTP POWAR JONNO OPEKHA KORUN... {P_WAIT_OTP}",
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
            await update.message.reply_text(f"{err_msg} {P_BAN}", parse_mode="HTML")
        return

    if text in ["⚙️ 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻", "ADMIN PANEL"] and user_id == ADMIN_ID:
        await send_admin_panel(update, context)
        return

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "ON" if is_bot_active() else "OFF"
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("VIEW ALL USER", callback_data="admin_view_users"), InlineKeyboardButton("DAILY STATS", callback_data="admin_daily_stats")],
        [InlineKeyboardButton("BAN USER", callback_data="admin_ban_start"), InlineKeyboardButton("Unban User", callback_data="admin_unban_start")],
        [InlineKeyboardButton(f"BOT STATUS: {status_str}", callback_data="admin_toggle_bot")]
    ])
    admin_text = (
        f"Admin Control Panel: {P_ADMIN_HEADER}\n"
        f"VIEW ALL USER {P_VIEW_USER}\n"
        f"DAILY STATS {P_STATS}\n"
        f"BAN USER {P_BAN}\n"
        f"UNBAN USER {P_UNBAN}"
    )
    if update.message:
        await update.message.reply_text(admin_text, parse_mode="HTML", reply_markup=admin_kb)
    elif update.callback_query:
        await update.callback_query.message.reply_text(admin_text, parse_mode="HTML", reply_markup=admin_kb)

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
            await query.message.reply_text(f"AKHONO OTP ASENI! {P_WAIT_OTP}")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        if id_num in active_orders:
            set_number_status(id_num, "bad")
            active_orders.pop(id_num, None)
            try:
                await query.message.delete()
            except Exception:
                pass

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
    set_number_status(id_num, "end")
    updated_user = get_user(uid)
    rem_bal = updated_user.get("balance", 0.0) if updated_user else 0.0

    success_text = (
        f"OTP RECEIVE SUCCESSFUL! {P_UNBAN}\n\n"
        f"Number: {phone} {P_PHONE}\n"
        f"OTP Code: {otp} {P_KEY_OTP}\n\n"
        f"Balance Deducted: {cost} USDT {P_BAL}\n"
        f"Remaining Balance: {rem_bal:.4f} USDT"
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

    group_forward_msg = (
        f"COUNTRY: {country_code.upper()} {country_flag}\n"
        f"NUMBER: {phone} {P_PHONE}\n"
        f"OTP: {otp} {P_KEY_OTP}"
    )

    if OTP_GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=OTP_GROUP_ID,
                text=group_forward_msg,
                parse_mode="HTML"
            )
        except Exception:
            pass

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

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Binance Pay", callback_data="pay_binance")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]
    ])
    msg = f"Payment Method select korun: {P_PAY_SELECT}\nBinance Pay {P_BINANCE}"
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(💵 𝙳𝙴𝙿𝙾𝚂𝙸𝚃|DEPOSIT)$"), deposit_start)],
        states={
            WAITING_AMOUNT: [CallbackQueryHandler(lambda u, c: None, pattern="^pay_binance$")]
        },
        fallbacks=[CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^cancel_flow_cb$")]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(deposit_conv)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    async with app:
        await app.start()
        await app.updater.start_polling()
        logging.info("Bot startup sequence completed. Polling started successfully.")
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
