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
    logging.error("MONGODB_URI Environment Variable missing!")
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
        [KeyboardButton("ACCOUNT BALANCE"), KeyboardButton("BY NUMBER")],
        [KeyboardButton("SET COUNTRIES"), KeyboardButton("SET SERVICE")],
        [KeyboardButton("MY PROFILE"), KeyboardButton("DEPOSIT")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("ADMIN PANEL")])
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
        await update.message.reply_text(f"{P_BAN} BANNED BY ADMIN. PLEASE CONTACT ADMIN.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("BOT UNDER MAINTENANCE BY ADMIN. Please try again later.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        msg = (
            f"Hello {user.full_name}!\n\n"
            f"{P_BAN} <b>YOU DO NOT HAVE AN ACTIVE SUBSCRIPTION FOR THE BOT!</b>\n"
            f"Bot bebohar korte chaile subscription nite hobe.\n\n"
            f"PRICE & VALIDITY (Rate: 125 Tk/$) :\n"
            f"• 5 Days = 50 Tk (or 0.40 USDT)\n"
            f"• 7 Days = 70 Tk (or 0.56 USDT)\n\n"
            f"Nicher button-e click kore subscription kinun:"
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
        f"WELCOME TO REX PRIVATE BOT!\n\n"
        f"RECENT SETUP:\n"
        f"• COUNTRIES: {curr_country} {country_flag}\n"
        f"• Service: {curr_service} {service_icon}\n"
        f"• YOUR BALANCE: {u_data.get('balance', 0.0):.4f} {P_BAL} USDT\n"
        f"• SUBSCRIPTION VALID TILL: Unlimited (Admin) {P_SUB_VALID}\n\n"
        f"Kaj korte niche dewa menu use korun:\n{P_WELCOME}"
    )
    await update.message.reply_text(welcome_msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id))

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    u_data = get_or_create_user(user_id, user.full_name)

    if u_data.get("is_banned", False):
        await update.message.reply_text(f"{P_BAN} YOUR ACCOUNT HAS BEEN BANNED BY ADMIN.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        return

    if not is_bot_active() and user_id != ADMIN_ID:
        await update.message.reply_text("BOT UNDER MAINTENANCE BY ADMIN. Try again after some time.")
        return

    if not is_subscribed(user_id):
        sub_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("5 Days (50 Tk / 0.40 USDT)", callback_data="buy_sub_5")],
            [InlineKeyboardButton("7 Days (70 Tk / 0.56 USDT)", callback_data="buy_sub_7")]
        ])
        await update.message.reply_text(f"{P_BAN} SUBSCRIPTION EXPIRED! BUY NEW SUBSCRIPTION.", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("BUY SUBSCRIPTION:", reply_markup=sub_kb)
        return

    text = update.message.text.strip()

    if text == "ACCOUNT BALANCE":
        bot_bal = u_data.get("balance", 0.0)
        msg = f"{P_BAL} <b>MY BALANCE:</b> {bot_bal:.4f} USDT"
        if user_id == ADMIN_ID:
            site_bal = get_vak_balance()
            msg += f"\n<b>PANEL BALANCE :</b> {site_bal:.4f} USD"
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    if text in ["MY PROFILE", "MY PORFILE"]:
        bot_bal = u_data.get("balance", 0.0)
        otp_cnt = u_data.get("otp_count", 0)
        exp_time = u_data.get("subscription_expiry")
        exp_str = exp_time.strftime("%Y-%m-%d %H:%M") if (exp_time and user_id != ADMIN_ID) else "Unlimited (Admin)"
        
        profile_msg = (
            f"Apnar Profile Info:\n{P_PROFILE_HEADER}\n"
            f"User ID: {user_id}\n{P_ID}\n"
            f"Name: {user.full_name}\n{P_NAME}\n"
            f"Balance: {bot_bal:.4f} USDT\n{P_BAL}\n"
            f"Total OTP Received: {otp_cnt}\n{P_OTP_RCV}\n"
            f"Subscription Valid: Unlimited (Admin)\n{P_SUB_EXP}"
        )
        await update.message.reply_text(profile_msg, parse_mode="HTML")
        return

    # COUNTRY SELECTION
    if text in ["SET COUNTRY", "SET COUNTRIES"]:
        country_kb = [
            [KeyboardButton("COUNTRY: HK (HONG KONG)"), KeyboardButton("COUNTRY: CHILE (CL)")],
            [KeyboardButton("MAIN MENU")]
        ]
        await update.message.reply_text("SELECT YOUR COUNTRY:", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True))
        return

    if "HK" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "hk"}})
        await update.message.reply_text(f"{P_UNBAN} Country set: HONG KONG (HK) {P_HK}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "CHILE" in text or "CL" in text:
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_country": "cl"}})
        await update.message.reply_text(f"{P_UNBAN} Country set: CHILE (CL) {P_CL}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    # SERVICE SELECTION
    if text == "SET SERVICE":
        service_kb = [
            [KeyboardButton("SERVICE: TG (TELEGRAM)")],
            [KeyboardButton("SERVICE: WA (WHATSAPP)")],
            [KeyboardButton("MAIN MENU")]
        ]
        await update.message.reply_text(f"SELECT YOUR SERVICE:\n{P_SELECT_SERVICE}", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True))
        return

    if "TG" in text or "TELEGRAM" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "tg"}})
        await update.message.reply_text(f"{P_UNBAN} SERVICE SET: TELEGRAM (TG) {P_TG}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if "WA" in text or "WHATSAPP" in text.upper():
        users_col.update_one({"user_id": user_id}, {"$set": {"selected_service": "wa"}})
        await update.message.reply_text(f"{P_UNBAN} SERVICE SET: WHATSAPP (WA) {P_WA}", parse_mode="HTML", reply_markup=get_main_keyboard(user_id))
        return

    if text == "MAIN MENU":
        await start(update, context)
        return

    if text == "BY NUMBER":
        user_has_active = any(order.get("user_id") == user_id for order in active_orders.values())
        if user_has_active:
            await update.message.reply_text(
                "<b>Allready ekta number kena royeche!</b>\nNotun number kenar age ager number-ti bebohar somponno korun othoba Cancel korun.",
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
                f"{P_BAN} <b>SORRY NOT ENOUGH BALANCE:</b> {bot_rate} USDT, YOUR BALANCE: {user_bal:.4f} USDT.\nPLEASE DEPOSIT.",
                parse_mode="HTML"
            )
            return

        status_msg = await update.message.reply_text(
            f"{country.upper()} {country_flag} BUYING NUMBER... WAIT A FEW SECONDS.",
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
                f"NUMBER PURCHASED SUCCESSFULLY!\n\n"
                f"Number: {phone_num}\n{P_PHONE}\n"
                f"ID Num: {id_num}\n{P_ID}\n"
                f"Country: {country.upper()} \n{country_flag}\n"
                f"Service: {service.upper()} \n{service_icon}\n"
                f"Rate: {bot_rate} USDT (OTP ASLEI BALANCE KATBE)\n\n"
                f"OTP POWAR JONNO OPEK KHA KORUN...\n{P_WAIT_OTP}",
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
            await update.message.reply_text(f"{P_BAN} {err_msg}", parse_mode="HTML")
        return

    if text == "ADMIN PANEL" and user_id == ADMIN_ID:
        await send_admin_panel(update, context)
        return

async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_str = "ON (Active)" if is_bot_active() else "OFF (Maintenance)"
    admin_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("VIEW ALL USER", callback_data="admin_view_users"), InlineKeyboardButton("DAILY STATS", callback_data="admin_daily_stats")],
        [InlineKeyboardButton("BAN USER", callback_data="admin_ban_start"), InlineKeyboardButton("Unban User", callback_data="admin_unban_start")],
        [InlineKeyboardButton("SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
        [InlineKeyboardButton("SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
        [InlineKeyboardButton("Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("ZERO BALANCE", callback_data="admin_zero_bal_start")],
        [InlineKeyboardButton("BROADCAST ALL", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(f"BOT STATUS: {status_str}", callback_data="admin_toggle_bot")]
    ])
    admin_text = (
        f"Admin Control Panel:\n{P_ADMIN_HEADER}\n\n"
        f"VIEW ALL USER\n{P_VIEW_USER}\n"
        f"DAILY STATS\n{P_STATS}\n"
        f"BAN USER\n{P_BAN}\n"
        f"UNBAN USER\n{P_UNBAN}\n"
        f"ADD BALANCE\n{P_ADD_BAL}\n"
        f"ZERO BALANCE\n{P_ZERO_BAL}\n"
        f"BROADCAST\n{P_BROADCAST}"
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

    if data == "admin_view_users" and user_id == ADMIN_ID:
        try:
            now = get_bd_time().replace(tzinfo=None)
            subscribed_users = list(users_col.find({
                "subscription_expiry": {"$gt": now}
            }))
            
            if not subscribed_users:
                await query.message.reply_text("Currently, there are no active subscribed users.")
                return
            
            msg = f"Active Subscribed Users ({len(subscribed_users)}):\n\n"
            for u in subscribed_users:
                uid = u.get("user_id", "N/A")
                safe_name = str(u.get("full_name", "User")).replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
                bal = u.get("balance", 0.0)
                otp_cnt = u.get("otp_count", 0)
                msg += f"• {safe_name} ({uid})\n  └ Balance: {bal:.4f} USDT | OTP Rcv: {otp_cnt}\n\n"
                
            await query.message.reply_text(msg)
        except Exception as e:
            await query.message.reply_text(f"Error loading users: {str(e)}")

    elif data == "admin_daily_stats" and user_id == ADMIN_ID:
        try:
            all_dates = otp_logs_col.distinct("date")
            today_str = get_bd_time().strftime("%Y-%m-%d")
            
            if today_str not in all_dates:
                all_dates.append(today_str)

            all_dates = sorted(all_dates, reverse=True)

            if not all_dates:
                await query.message.reply_text("Kono OTP log ekhono nei.")
                return

            dates_kb = []
            for d in all_dates[:10]:
                label = f"Date: {d} {'(Today)' if d == today_str else ''}"
                dates_kb.append([InlineKeyboardButton(label, callback_data=f"view_stats_{d}")])

            await query.message.reply_text(
                "DAILY OTP STATS ARCHIVE\n\n"
                "Nicher tarikh (date) gulo theke click kore jekono diner report dekhte paren:",
                reply_markup=InlineKeyboardMarkup(dates_kb)
            )
        except Exception as e:
            await query.message.reply_text(f"Error generating stats: {str(e)}")

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
                f"OTP STATS FOR {target_date}:\n\n"
                f"Total OTP Received: {total_count}\n"
                f"────────────────────\n"
                f"Hong Kong (HK): {hk_count}\n"
                f"Chile (CL): {cl_count}\n"
                f"Others: {other_count}\n"
                f"────────────────────\n"
                f"WhatsApp (WA): {wa_count}\n"
                f"Telegram (TG): {tg_count}"
            )
            await query.message.reply_text(stats_msg)
        except Exception as e:
            await query.message.reply_text(f"Error loading stats for {target_date}: {str(e)}")

    elif data == "admin_toggle_bot" and user_id == ADMIN_ID:
        current_status = is_bot_active()
        new_status = not current_status
        set_bot_active(new_status)
        status_text = "Bot ON (Active) kora hoyeche!" if new_status else "Bot OFF (Maintenance Mode) kora hoyeche!"
        
        status_str = "ON (Active)" if new_status else "OFF (Maintenance)"
        admin_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("VIEW ALL USER", callback_data="admin_view_users"), InlineKeyboardButton("DAILY STATS", callback_data="admin_daily_stats")],
            [InlineKeyboardButton("BAN USER", callback_data="admin_ban_start"), InlineKeyboardButton("Unban User", callback_data="admin_unban_start")],
            [InlineKeyboardButton("SET HK WA PRICE", callback_data="admin_rate_wa_hk_start"), InlineKeyboardButton("SET CL WA PRICE", callback_data="admin_rate_wa_cl_start")],
            [InlineKeyboardButton("SET HK TG PRICE", callback_data="admin_rate_tg_hk_start"), InlineKeyboardButton("SET CL TG PRICE", callback_data="admin_rate_tg_cl_start")],
            [InlineKeyboardButton("Add Balance", callback_data="admin_add_bal_start"), InlineKeyboardButton("ZERO BALANCE", callback_data="admin_zero_bal_start")],
            [InlineKeyboardButton("BROADCAST ALL", callback_data="admin_broadcast_start")],
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
            await query.message.reply_text("AKHONO OTP ASENI, AKTU PORE ABAR TRY KORUN.")

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
                await query.message.reply_text("ORDER NOT ACTIVE OR OTP ALREADY RECEIVED.")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        users_col.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{P_UNBAN} Approved & Balance Added!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text=f"Apnar {amount} USDT deposit shofolbhabe jukto kora hoyeche!")

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{P_BAN} Deposit Rejected!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text="Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        user_doc = users_col.find_one({"user_id": target_id})
        days = user_doc.get("sub_days", 5) if user_doc else 5
        expiry_date = get_bd_time().replace(tzinfo=None) + timedelta(days=days)
        users_col.update_one({"user_id": target_id}, {"$set": {"subscription_expiry": expiry_date}})
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{P_UNBAN} Subscription Approved ({days} Days Active)!", parse_mode="HTML")
        
        await context.bot.send_message(
            chat_id=target_id,
            text=f"Apnar Subscription Approved hoyeche! {days} Diner jonno bot-er sob features active kora hoyeche.",
            reply_markup=get_main_keyboard(target_id)
        )

    elif data.startswith("reject_sub_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + f"\n\n{P_BAN} Subscription Rejected!", parse_mode="HTML")
        await context.bot.send_message(chat_id=target_id, text="Apnar subscription request-ti batil kora hoyeche.")

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
        f"{P_UNBAN} <b>OTP RECEIVE SUCCESSFUL!</b>\n\n"
        f"NUMBER: <code>{phone}</code>\n"
        f"OTP CODE: <code>{otp}</code>\n\n"
        f"{P_BAL} BALANCE DEDUCTED: {cost} USDT\n"
        f"REMAINING BALANCE: {rem_bal:.4f} USDT"
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
        f"COUNTRY: {country_code.upper()} {P_CL}\n{P_GROUP_HEADER_1}\n{P_WA}\n"
        f"NUMBER: {masked_phone}\n{P_PHONE}\n"
        f"OTP: {otp}\n{P_KEY_OTP}\n"
        f"Message: YOUR {service_type} CODE: {otp}\n{P_GROUP_HEADER_1}\n{P_WA}"
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

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Binance Pay", callback_data="pay_binance")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]
    ])
    msg = (
        f"Payment Method select korun:\n{P_PAY_SELECT}\n"
        f"Binance pay\n{P_BINANCE}\n\n"
        f"Cancel\n{P_CANCEL}"
    )
    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=payment_kb)
    return WAITING_AMOUNT

async def deposit_binance_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]])
    await query.message.reply_text("Apni koto USDT pathaben ta likhe janan (Minimum: 1 USDT, jemon: 1, 2.5, 5):", reply_markup=cancel_kb)
    return WAITING_AMOUNT

async def deposit_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount < 1.0:
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]])
            await update.message.reply_text("Minimum deposit amount 1 USDT. Doya kore 1 ba tar besi amount likhun.", reply_markup=cancel_kb)
            return WAITING_AMOUNT

        context.user_data["dep_amount"] = amount
        
        msg = (
            f"Deposit Amount: {amount} USDT\n\n"
            f"Nicher Binance Pay ID-te Binance app theke Pay/Send Money Korun:\n"
            f"{P_BINANCE} Binance Pay ID: {BINANCE_ID}\n\n"
            f"Note: Minimum deposit 1 USDT. Binance Pay-er madhyome kono extra fee charai pathano jabe.\n\n"
            f"Dollar pathanor por apnar Order ID / TxID-ti likhe message din:"
        )
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=cancel_kb)
        return WAITING_TXID
    except ValueError:
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]])
        await update.message.reply_text("Sothik shongkha likhun (jemon: 1 ba 5).", reply_markup=cancel_kb)
        return WAITING_AMOUNT

async def deposit_txid_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txid = update.message.text.strip()
    context.user_data["dep_txid"] = txid
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_flow_cb")]])
    await update.message.reply_text("Ekhon apnar payment-er screenshot (Photo) Pathan:", reply_markup=cancel_kb)
    return WAITING_SCREENSHOT

async def deposit_screenshot_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo = update.message.photo[-1]
    amount = context.user_data.get("dep_amount")
    txid = context.user_data.get("dep_txid")

    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve_dep_{user.id}_{amount}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_dep_{user.id}")
        ]
    ])

    caption = (
        f"Notun Deposit Request!\n\n"
        f"User: {user.full_name} ({user.id})\n"
        f"Amount: {amount} USDT\n"
        f"TxID: {txid}"
    )

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=photo.file_id, caption=caption, parse_mode="HTML", reply_markup=admin_kb)
    await update.message.reply_text(f"{P_UNBAN} Apnar deposit request admin-er kache pathano hoyeche! Jaachai kore druto balance jukto kora hobe.", parse_mode="HTML")
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
        await update.message.reply_text("Process batil kora hoyeche.")
    return ConversationHandler.END

async def run_bot():
    app = Application.builder().token(BOT_TOKEN).build()

    deposit_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^DEPOSIT$"), deposit_start)],
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
