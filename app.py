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
    ConversationHandler,
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

# যে গ্রুপে OTP ফরওয়ার্ড হবে
OTP_GROUP_ID = os.getenv("OTP_GROUP_ID") 

USD_TO_BDT = 125.0

# Render Keep-Alive
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "Bot is Alive & Running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# Databases
users_db = {}
pending_deposits = {}

# Custom Service Prices in BDT (শুধু Telegram ও WhatsApp)
CUSTOM_PRICES_BDT = {
    "Telegram ✈️": {"code": "tg", "price_bdt": 60.0},
    "WhatsApp 💬": {"code": "wa", "price_bdt": 75.0},
}

WAITING_METHOD, WAITING_DETAILS, WAITING_PROOF = range(3)


def get_user_data(user_id: int) -> dict:
    if user_id not in users_db:
        users_db[user_id] = {
            "balance_bdt": 0.0,
            "expiry": None,
            "approved": False,
            "banned": False,
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
    """মেসেজ কিবোর্ড তৈরি করবে যা ফোনের নিচে ফিক্সড থাকবে"""
    keyboard = [
        [KeyboardButton("✈️ Telegram - ৳60"), KeyboardButton("💬 WhatsApp - ৳75")],
        [KeyboardButton("💰 Deposit Balance"), KeyboardButton("🔄 Refresh Menu")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("⚙️ Admin Panel")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user_data(user.id)

    if u_data.get("banned", False) and user.id != ADMIN_ID:
        await update.message.reply_text("❌ আপনাকে বট থেকে ব্যান করা হয়েছে।")
        return

    is_admin = (user.id == ADMIN_ID)
    is_active = check_and_update_membership(user.id)
    balance_bdt = u_data["balance_bdt"]

    main_markup = build_main_keyboard(user.id)

    if is_admin or is_active:
        expiry_info = "👑 **Admin Unlimited Access**" if is_admin else f"⏳ **মেম্বারশিপ মেয়াদ:** {u_data['expiry'].strftime('%Y-%m-%d %H:%M') if u_data.get('expiry') else 'N/A'}"
        
        msg = (
            f"👋 **স্বাগতম {user.first_name}!**\n\n"
            f"💳 **ব্যালেন্স:** ৳{balance_bdt:.2f} BDT\n"
            f"{expiry_info}\n\n"
            f"নিচের মেনু বাটন থেকে সার্ভিস নির্বাচন করুন:"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_markup)
    else:
        msg = (
            f"👋 **স্বাগতম {user.first_name}!**\n\n"
            f"⚠️ **আপনার ৩ দিনের মেম্বারশিপ সক্রিয় নেই।**\n"
            f"মেম্বারশিপ কিনতে বা ডিপোজিট করতে নিচের মেনু ব্যবহার করুন।\n\n"
            f"💵 **ফি:** ৳৩০ BDT\n"
            f"💳 **আপনার ব্যালেন্স:** ৳{balance_bdt:.2f} BDT"
        )
        sub_keyboard = [
            [KeyboardButton("🛒 Buy 3-Days Membership (৳30)")],
            [KeyboardButton("💰 Deposit Balance"), KeyboardButton("🔄 Refresh Menu")]
        ]
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(sub_keyboard, resize_keyboard=True)
        )


# --- TEXT BUTTON HANDLER ---

async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    u_data = get_user_data(user_id)

    if text == "🔄 Refresh Menu":
        await start(update, context)
        return

    if text == "⚙️ Admin Panel":
        if user_id != ADMIN_ID:
            return
        admin_msg = (
            "⚙️ **Admin Control Panel**\n\n"
            "কমান্ডসমূহ টাইপ করে ব্যবহার করুন:\n"
            "• `/users` - ইউজারের তালিকা\n"
            "• `/setprice CODE BDT` - দাম পরিবর্তন (যেমন: `/setprice tg 50`)\n"
            "• `/ban USER_ID` - ইউজার ব্যান করা\n"
            "• `/unban USER_ID` - ব্যান তোলা\n"
            "• `/addbalance USER_ID BDT` - ব্যালেন্স দেওয়া"
        )
        await update.message.reply_text(admin_msg, parse_mode="Markdown")
        return

    if text == "🛒 Buy 3-Days Membership (৳30)":
        fee_bdt = 30.0
        if check_and_update_membership(user_id) and user_id != ADMIN_ID:
            await update.message.reply_text("আপনার মেম্বারশিপ ইতিমধ্যেই সক্রিয় আছে!")
            return

        if u_data["balance_bdt"] < fee_bdt:
            await update.message.reply_text(
                f"❌ পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳৩০, আপনার আছে: ৳{u_data['balance_bdt']:.2f}\n\nপ্রথমে Deposit Balance বাটনে চাপুন।"
            )
            return

        u_data["balance_bdt"] -= fee_bdt
        u_data["approved"] = True
        u_data["expiry"] = datetime.now() + timedelta(days=3)

        await update.message.reply_text(
            f"🎉 ৩ দিনের মেম্বারশিপ চালু হয়েছে!\nঅবশিষ্ট ব্যালেন্স: ৳{u_data['balance_bdt']:.2f}",
            reply_markup=build_main_keyboard(user_id)
        )
        return

    if text == "💰 Deposit Balance":
        dep_keyboard = [
            [KeyboardButton("bKash (BDT)"), KeyboardButton("Binance (USDT)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            "পেমেন্ট মেথড সিলেক্ট করুন:",
            reply_markup=ReplyKeyboardMarkup(dep_keyboard, resize_keyboard=True)
        )
        return

    # সার্ভিস কেনা চেক (Telegram / WhatsApp)
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
            await update.message.reply_text("আপনার মেম্বারশিপ শেষ হয়ে গেছে! প্রথমে মেম্বারশিপ কিনুন।")
            return

        if user_id != ADMIN_ID and u_data["balance_bdt"] < price_bdt:
            await update.message.reply_text(
                f"❌ পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳{price_bdt:.2f} BDT\nবর্তমান ব্যালেন্স: ৳{u_data['balance_bdt']:.2f} BDT"
            )
            return

        url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service_code}&country=ru"
        try:
            res = requests.get(url).json()
            if "tel" in res and "idNum" in res:
                if user_id != ADMIN_ID:
                    u_data["balance_bdt"] -= price_bdt

                phone_num = res["tel"]
                id_num = res["idNum"]

                await update.message.reply_text(
                    f"✅ **নম্বর নেওয়া সফল হয়েছে!**\n\n"
                    f"সার্ভিস: {service_name}\n"
                    f"কাটা হয়েছে: ৳{price_bdt:.2f} BDT\n"
                    f"অবশিষ্ট ব্যালেন্স: ৳{u_data['balance_bdt']:.2f} BDT\n\n"
                    f"📱 **নম্বর:** `{phone_num}`\n"
                    f"🆔 **ID Num:** `{id_num}`\n\n"
                    f"OTP চেক করতে টাইপ করুন: `/check_{id_num}`",
                    parse_mode="Markdown"
                )

                asyncio.create_task(
                    poll_otp_and_forward(context, id_num, phone_num, service_name, user_id)
                )
            else:
                await update.message.reply_text(f"নম্বর পাওয়া যায়নি: {res.get('error', 'অজানা সমস্যা')}")
        except Exception as e:
            await update.message.reply_text(f"API এরর: {str(e)}")


# --- MANUAL OTP CHECK ---

async def check_otp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("/check_"):
        id_num = text.replace("/check_", "").strip()
        url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
        try:
            res = requests.get(url).json()
            if "smsCode" in res and res["smsCode"]:
                await update.message.reply_text(f"🔑 **আপনার OTP কোড:** `{res['smsCode']}`", parse_mode="Markdown")
            else:
                await update.message.reply_text("এখনো কোনো SMS আসেনি। আবার চেষ্টা করুন।")
        except Exception as e:
            await update.message.reply_text(f"এরর: {str(e)}")


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
                        text=f"🔑 **আপনার OTP কোড:** `{otp_code}`\n📱 **নম্বর:** `{phone_num}`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                if OTP_GROUP_ID:
                    try:
                        forward_msg = (
                            f"📩 **নতুন OTP প্রাপ্তি!**\n\n"
                            f"🛠 **সার্ভিস:** {service_name}\n"
                            f"📱 **নম্বর:** `{phone_num}`\n"
                            f"🔑 **OTP Code:** `{otp_code}`\n"
                            f"👤 **ইউজার ID:** `{user_id}`"
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


# --- ADMIN COMMANDS ---

async def set_price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2:
        await update.message.reply_text("ফরম্যাট: `/setprice CODE BDT` (যেমন: `/setprice tg 50`)", parse_mode="Markdown")
        return
    code = context.args[0].lower()
    try:
        new_price = float(context.args[1])
        if code == "tg":
            CUSTOM_PRICES_BDT["Telegram ✈️"]["price_bdt"] = new_price
            await update.message.reply_text(f"✅ Telegram-এর দাম ৳{new_price:.2f} করা হয়েছে।")
        elif code == "wa":
            CUSTOM_PRICES_BDT["WhatsApp 💬"]["price_bdt"] = new_price
            await update.message.reply_text(f"✅ WhatsApp-এর দাম ৳{new_price:.2f} করা হয়েছে।")
        else:
            await update.message.reply_text("ইনভ্যালিড কোড! (tg অথবা wa)")
    except ValueError:
        await update.message.reply_text("সঠিক দাম লিখুন।")


async def list_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not users_db:
        await update.message.reply_text("কোনো ইউজার ডাটা নেই।")
        return
    msg = "📋 **ইউজার তালিকা:**\n\n"
    for uid, uinfo in users_db.items():
        status = "BANNED 🚫" if uinfo.get("banned") else ("ACTIVE 🟢" if check_and_update_membership(uid) else "EXPIRED 🔴")
        msg += f"• ID: `{uid}` | BDT: ৳{uinfo['balance_bdt']:.1f} | {status}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")


def main():
    threading.Thread(target=run_flask, daemon=True).start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("users", list_users_cmd))
    app.add_handler(CommandHandler("setprice", set_price_cmd))
    app.add_handler(MessageHandler(filters.Regex("^/check_"), check_otp_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))

    print("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
