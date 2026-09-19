import logging
import os
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import requests

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY")
ADMIN_BKASH = os.getenv("ADMIN_BKASH", "Not Set")
ADMIN_BINANCE = os.getenv("ADMIN_BINANCE", "Not Set")
OTP_GROUP_ID = os.getenv("OTP_GROUP_ID") 

USD_TO_BDT = 125.0

# Render Keep-Alive Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "Bot is Alive & Running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# Databases
users_db = {}
# Active country set by admin (Default: ru = Russia, us = USA, id = Indonesia)
active_country = "ru" 

CUSTOM_PRICES_BDT = {
    "Telegram ✈️": {"code": "tg", "price_bdt": 60.0},
    "WhatsApp 💬": {"code": "wa", "price_bdt": 75.0},
}


def get_user_data(user_id: int) -> dict:
    if user_id not in users_db:
        users_db[user_id] = {
            "balance_bdt": 0.0,
            "expiry": None,
            "approved": False,
            "banned": False,
            "last_id_num": None,
        }
    return users_db[user_id]


def check_and_update_membership(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True

    user = get_user_data(user_id)
    if user.get("banned", False):
        return False

    if user.get("approved", False) and user.get("expiry"):
        if datetime.now() > user["expiry"]:
            user["approved"] = False
            user["expiry"] = None
            return False
        return True

    return False


def build_main_keyboard(user_id: int):
    keyboard = [
        [KeyboardButton("🛒 Buy Number")],
        [KeyboardButton("💰 Deposit Balance"), KeyboardButton("🔄 Refresh Menu")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user_data(user.id)

    if u_data.get("banned", False) and user.id != ADMIN_ID:
        await update.message.reply_text("❌ Aapnake bot theke ban kora hoyeche.")
        return

    is_admin = (user.id == ADMIN_ID)
    is_active = check_and_update_membership(user.id)
    balance_bdt = u_data["balance_bdt"]

    main_markup = build_main_keyboard(user.id)

    if is_admin or is_active:
        expiry_info = "👑 **Admin Unlimited Access**" if is_admin else f"⏳ **Membership Expiry:** {u_data['expiry'].strftime('%Y-%m-%d %H:%M') if u_data.get('expiry') else 'N/A'}"
        
        msg = (
            f"👋 **Swagotom {user.first_name}!**\n\n"
            f"💳 **Balance:** ৳{balance_bdt:.2f} BDT\n"
            f"{expiry_info}\n"
            f"🌍 **Active Country:** `{active_country.upper()}`\n\n"
            f"Nicher menu button theke option select korun:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_markup)
    else:
        msg = (
            f"👋 **Swagotom {user.first_name}!**\n\n"
            f"⚠️ **Aapnar 3 diner membership active nei.**\n"
            f"Membership kinte nicher menu babohar korun.\n\n"
            f"💵 **Fee:** ৳30 BDT\n"
            f"💳 **Aapnar Balance:** ৳{balance_bdt:.2f} BDT"
        )
        sub_keyboard = [
            [KeyboardButton("🛒 Buy 3-Days Membership (৳30)")],
            [KeyboardButton("💰 Deposit Balance"), KeyboardButton("🔄 Refresh Menu")]
        ]
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(sub_keyboard, resize_keyboard=True)
        )


def get_country_capacity(service_code: str, country_code: str):
    """VAK-SMS theke specific country-r stock/capacity check korbe"""
    url = f"https://vak-sms.com/api/getMiniNum/?apiKey={VAK_SMS_API_KEY}&service={service_code}&country={country_code}"
    try:
        res = requests.get(url).json()
        if "count" in res:
            return res["count"]
        elif service_code in res:
            return res[service_code]
        return 0
    except Exception:
        return 0


# --- BUTTON NAVIGATION & ACTIONS ---

async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_country
    user_id = update.effective_user.id
    text = update.message.text.strip()
    u_data = get_user_data(user_id)

    if text in ["🔄 Refresh Menu", "🔙 Main Menu"]:
        await start(update, context)
        return

    # Buy Number Menu & Stock Capacity Show
    if text == "🛒 Buy Number":
        tg_cap = get_country_capacity("tg", active_country)
        wa_cap = get_country_capacity("wa", active_country)

        buy_keyboard = [
            [KeyboardButton(f"✈️ Telegram ({tg_cap} Left) - ৳60"), KeyboardButton(f"💬 WhatsApp ({wa_cap} Left) - ৳75")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            f"📱 **Selected Country: `{active_country.upper()}`**\n"
            f"Nicher available stock dekhe service select korun:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(buy_keyboard, resize_keyboard=True)
        )
        return

    # Check OTP Action
    if text == "📩 Check Last OTP":
        id_num = u_data.get("last_id_num")
        if not id_num:
            await update.message.reply_text("❌ Aapnar kono active number order nei.")
            return

        url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
        try:
            res = requests.get(url).json()
            if "smsCode" in res and res["smsCode"]:
                await update.message.reply_text(f"🔑 **Aapnar OTP Code:** `{res['smsCode']}`", parse_mode="Markdown")
            else:
                await update.message.reply_text("⏳ Ekhono kono SMS aseni. Ektu por Check OTP-te chapun.")
        except Exception as e:
            await update.message.reply_text(f"Error: {str(e)}")
        return

    # Admin Panel Actions
    if text == "⚙️ Admin Panel":
        if user_id != ADMIN_ID:
            return
        admin_keyboard = [
            [KeyboardButton("📋 View Users List")],
            [KeyboardButton("🌐 Set Country Code")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            f"⚙️ **Admin Control Panel**\n\nCurrent Active Country: `{active_country}`", 
            parse_mode="Markdown", 
            reply_markup=ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True)
        )
        return

    if text == "🌐 Set Country Code":
        if user_id != ADMIN_ID:
            return
        await update.message.reply_text(
            "Country code set korar jonno type korun:\n`/setcountry CODE`\n\nExample:\n`/setcountry ru` (Russia)\n`/setcountry us` (USA)\n`/setcountry id` (Indonesia)",
            parse_mode="Markdown"
        )
        return

    if text == "📋 View Users List":
        if user_id != ADMIN_ID:
            return
        if not users_db:
            await update.message.reply_text("Kono user data nei.")
            return
        msg = "📋 **User List:**\n\n"
        for uid, uinfo in users_db.items():
            status = "BANNED 🚫" if uinfo.get("banned") else ("ACTIVE 🟢" if check_and_update_membership(uid) else "EXPIRED 🔴")
            msg += f"• ID: `{uid}` | BDT: ৳{uinfo['balance_bdt']:.1f} | {status}\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if text == "🛒 Buy 3-Days Membership (৳30)":
        fee_bdt = 30.0
        if check_and_update_membership(user_id) and user_id != ADMIN_ID:
            await update.message.reply_text("Aapnar membership itomoddhe active ache!")
            return

        if u_data["balance_bdt"] < fee_bdt:
            await update.message.reply_text(
                f"❌ Porjapto balance nei!\nProyojon: ৳30, Aapnar ache: ৳{u_data['balance_bdt']:.2f}\n\nDeposit Balance-e chapun."
            )
            return

        u_data["balance_bdt"] -= fee_bdt
        u_data["approved"] = True
        u_data["expiry"] = datetime.now() + timedelta(days=3)

        await update.message.reply_text(
            f"🎉 3 diner membership chalu hoyeche!\nOboshishto Balance: ৳{u_data['balance_bdt']:.2f}",
            reply_markup=build_main_keyboard(user_id)
        )
        return

    if text == "💰 Deposit Balance":
        dep_keyboard = [
            [KeyboardButton("bKash (BDT)"), KeyboardButton("Binance (USDT)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            "Payment method select korun:",
            reply_markup=ReplyKeyboardMarkup(dep_keyboard, resize_keyboard=True)
        )
        return

    # Number Buying Logic
    service_code = None
    service_name = ""
    price_bdt = 0.0

    if "Telegram" in text:
        service_code = "tg"
        service_name = "Telegram ✈️"
        price_bdt = CUSTOM_PRICES_BDT["Telegram ✈️"]["price_bdt"]
    elif "WhatsApp" in text:
        service_code = "wa"
        service_name = "WhatsApp 💬"
        price_bdt = CUSTOM_PRICES_BDT["WhatsApp 💬"]["price_bdt"]

    if service_code:
        if not check_and_update_membership(user_id):
            await update.message.reply_text("Aapnar membership shesh hoye geche!")
            return

        if user_id != ADMIN_ID and u_data["balance_bdt"] < price_bdt:
            await update.message.reply_text(
                f"❌ Porjapto balance nei!\nProyojon: ৳{price_bdt:.2f} BDT\nBortoman Balance: ৳{u_data['balance_bdt']:.2f} BDT"
            )
            return

        # Fetch number based on Admin's selected country
        url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service_code}&country={active_country}"
        try:
            res = requests.get(url).json()
            if "tel" in res and "idNum" in res:
                if user_id != ADMIN_ID:
                    u_data["balance_bdt"] -= price_bdt

                phone_num = res["tel"]
                id_num = res["idNum"]
                u_data["last_id_num"] = id_num

                otp_keyboard = [
                    [KeyboardButton("📩 Check Last OTP")],
                    [KeyboardButton("🛒 Buy Number"), KeyboardButton("🔙 Main Menu")]
                ]

                await update.message.reply_text(
                    f"✅ **Number Order Successful!**\n\n"
                    f"Service: {service_name}\n"
                    f"Country: `{active_country.upper()}`\n"
                    f"Cost: ৳{price_bdt:.2f} BDT\n"
                    f"Balance: ৳{u_data['balance_bdt']:.2f} BDT\n\n"
                    f"📱 **Number:** `{phone_num}`\n"
                    f"🆔 **ID Num:** `{id_num}`\n\n"
                    f"OTP pete **Check Last OTP** button-e chapun.",
                    parse_mode="Markdown",
                    reply_markup=ReplyKeyboardMarkup(otp_keyboard, resize_keyboard=True)
                )

                asyncio.create_task(
                    poll_otp_and_forward(context, id_num, phone_num, service_name, user_id)
                )
            else:
                err_msg = res.get('error', 'noNumber')
                await update.message.reply_text(
                    f"❌ Country `{active_country.upper()}`-e ekhon {service_name} number stock nei ({err_msg}). Admin-ke country change korte bolun."
                )
        except Exception as e:
            await update.message.reply_text(f"API Error: {str(e)}")


# --- ADMIN COUNTRY COMMAND ---

async def set_country_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_country
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Format: `/setcountry CODE` (Example: `/setcountry ru`)", parse_mode="Markdown")
        return
    
    active_country = context.args[0].lower()
    await update.message.reply_text(f"✅ Bot Country successfuly changed to: `{active_country.upper()}`", parse_mode="Markdown")


# --- BACKGROUND OTP POLLING ---

async def poll_otp_and_forward(context: ContextTypes.DEFAULT_TYPE, id_num: str, phone_num: str, service_name: str, user_id: int):
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
    for _ in range(100):
        await asyncio.sleep(6)
        try:
            res = requests.get(url).json()
            if "smsCode" in res and res["smsCode"]:
                otp_code = res["smsCode"]
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🔑 **Aapnar OTP Code:** `{otp_code}`\n📱 **Number:** `{phone_num}`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                if OTP_GROUP_ID:
                    try:
                        forward_msg = (
                            f"📩 **Notun OTP Prapti!**\n\n"
                            f"🛠 **Service:** {service_name}\n"
                            f"📱 **Number:** `{phone_num}`\n"
                            f"🔑 **OTP Code:** `{otp_code}`\n"
                            f"👤 **User ID:** `{user_id}`"
                        )
                        await context.bot.send_message(
                            chat_id=OTP_GROUP_ID,
                            text=forward_msg,
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        logging.error(f"Failed to forward OTP: {err}")
                break
        except Exception as e:
            logging.error(f"Error checking OTP: {e}")


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setcountry", set_country_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))

    print("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
