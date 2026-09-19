import logging
import os
import threading
import asyncio
from flask import Flask, request, jsonify
from telegram import ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import requests

# Logging Configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
# স্ক্রিনশটের দেওয়া আপনার VAK-SMS API Key
VAK_SMS_API_KEY = os.getenv("VAK_SMS_API_KEY", "893d842ab70a4e79b4ad323185a69257")

# Flask Web Server Keep-Alive (For Render/Replit)
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "VAK-SMS Telegram Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# Global State Variables
user_selected_country = {}
user_selected_service = {}
active_orders = {}

# Menu Keyboards
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("💳 Account Balance"), KeyboardButton("🛒 Buy Number")],
        [KeyboardButton("🌐 Set Country"), KeyboardButton("📱 Set Service")],
        [KeyboardButton("📩 Check Active OTP"), KeyboardButton("❌ Cancel Number")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Helper Functions for VAK-SMS API Calls
def get_vak_balance():
    url = f"https://vak-sms.com/api/getBalance/?apiKey={VAK_SMS_API_KEY}"
    try:
        res = requests.get(url).json()
        if "balance" in res:
            return f"${res['balance']:.4f} USD"
        return "Error fetching balance"
    except Exception as e:
        return f"API Error: {str(e)}"

def buy_vak_number(service: str, country: str):
    url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service}&country={country}"
    try:
        res = requests.get(url).json()
        return res
    except Exception as e:
        return {"error": str(e)}

def fetch_otp_code(id_num: str):
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
    try:
        res = requests.get(url).json()
        return res
    except Exception as e:
        return {"error": str(e)}

def set_number_status(id_num: str, status: str):
    # status: 'bad' (cancel/reject), 'end' (successfully finished)
    url = f"https://vak-sms.com/api/setStatus/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}&status={status}"
    try:
        res = requests.get(url).json()
        return res
    except Exception as e:
        return {"error": str(e)}


# Command & Message Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_selected_country:
        user_selected_country[user_id] = "hk" # Default Hong Kong
    if user_id not in user_selected_service:
        user_selected_service[user_id] = "wa" # Default WhatsApp

    c_code = user_selected_country[user_id].upper()
    s_code = user_selected_service[user_id].upper()

    welcome_msg = (
        f"👋 **VAK-SMS Full Account Bot-এ স্বাগতম!**\n\n"
        f"⚙️ **বর্তমান কনফিগারেশন:**\n"
        f"• Country Code: `{c_code}`\n"
        f"• Service: `{s_code}`\n\n"
        f"নিচের মেনু থেকে যেকোনো অপশন বেছে নিন:"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())


async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # 1. Check Account Balance
    if text == "💳 Account Balance":
        balance = get_vak_balance()
        await update.message.reply_text(f"💰 **আপনার VAK-SMS ব্যালেন্স:** `{balance}`", parse_mode="Markdown")
        return

    # 2. Set Country
    if text == "🌐 Set Country":
        country_kb = [
            [KeyboardButton("Country: HK (Hong Kong)"), KeyboardButton("Country: US (USA)")],
            [KeyboardButton("Country: RU (Russia)"), KeyboardButton("Country: IN (India)")],
            [KeyboardButton("Country: ID (Indonesia)"), KeyboardButton("Country: PH (Philippines)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            "🌐 **দেশ নির্বাচন করুন অথবা কান্ট্রি কোড লিখে পাঠান (যেমন: `hk`, `us`, `ru`):**",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(country_kb, resize_keyboard=True)
        )
        return

    if text.startswith("Country:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_country[user_id] = code
        await update.message.reply_text(f"✅ Country সেট করা হয়েছে: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    # 3. Set Service
    if text == "📱 Set Service":
        service_kb = [
            [KeyboardButton("Service: WA (WhatsApp)"), KeyboardButton("Service: TG (Telegram)")],
            [KeyboardButton("Service: IG (Instagram)"), KeyboardButton("Service: GO (Google/Gmail)")],
            [KeyboardButton("Service: IM (Imo)"), KeyboardButton("Service: VI (Viber)")],
            [KeyboardButton("🔙 Main Menu")]
        ]
        await update.message.reply_text(
            "📱 **সার্ভিস নির্বাচন করুন অথবা শর্ট কোড পাঠান (যেমন: `wa`, `tg`, `go`):**",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup(service_kb, resize_keyboard=True)
        )
        return

    if text.startswith("Service:"):
        code = text.split(":")[1].split("(")[0].strip().lower()
        user_selected_service[user_id] = code
        await update.message.reply_text(f"✅ Service সেট করা হয়েছে: `{code.upper()}`", parse_mode="Markdown", reply_markup=get_main_keyboard())
        return

    if text == "🔙 Main Menu":
        await start(update, context)
        return

    # 4. Buy Number
    if text == "🛒 Buy Number":
        country = user_selected_country.get(user_id, "hk")
        service = user_selected_service.get(user_id, "wa")

        await update.message.reply_text(f"⏳ `{country.upper()}` দেশের জন্য `{service.upper()}` নম্বর কেনা হচ্ছে...")

        res = buy_vak_number(service, country)

        if isinstance(res, dict) and "tel" in res and "idNum" in res:
            phone_num = res["tel"]
            id_num = res["idNum"]
            active_orders[user_id] = id_num

            await update.message.reply_text(
                f"✅ **নম্বর কেনা সফল হয়েছে!**\n\n"
                f"📱 **নম্বর:** `{phone_num}`\n"
                f"🆔 **Order ID:** `{id_num}`\n"
                f"🌍 **Country:** `{country.upper()}`\n"
                f"💬 **Service:** `{service.upper()}`\n\n"
                f"ওটিপি চেক করতে **📩 Check Active OTP** বাটনে চাপুন।",
                parse_mode="Markdown",
                reply_markup=get_main_keyboard()
            )

            # Background Task for Auto OTP Checking
            asyncio.create_task(auto_check_otp(context, user_id, id_num, str(phone_num)))
        else:
            err_msg = res.get("error", "Unknown Error / Out of Stock") if isinstance(res, dict) else "Error"
            await update.message.reply_text(f"❌ **নম্বর কেনা সম্ভব হয়নি:** `{err_msg}`", parse_mode="Markdown")
        return

    # 5. Manual OTP Check
    if text == "📩 Check Active OTP":
        id_num = active_orders.get(user_id)
        if not id_num:
            await update.message.reply_text("❌ আপনার কোনো অ্যাক্টিভ নম্বর অর্ডার নেই।")
            return

        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            await update.message.reply_text(f"🔑 **আপনার OTP কোড:** `{res['smsCode']}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⏳ এখনো কোনো OTP আসেনি। একটু পর আবার চেষ্টা করুন।")
        return

    # 6. Cancel Number
    if text == "❌ Cancel Number":
        id_num = active_orders.get(user_id)
        if not id_num:
            await update.message.reply_text("❌ ক্যান্সেল করার মতো কোনো অ্যাক্টিভ নম্বর নেই।")
            return

        res = set_number_status(id_num, "bad")
        active_orders.pop(user_id, None)
        await update.message.reply_text("✅ নম্বরটি ক্যান্সেল করা হয়েছে এবং ব্যালেন্স রিফান্ড করা হয়েছে।")
        return

    # Direct code input fallback
    if len(text) <= 3:
        user_selected_country[user_id] = text.lower()
        await update.message.reply_text(f"✅ Country code updated to: `{text.upper()}`", parse_mode="Markdown")


# Background Worker to poll OTP automatically
async def auto_check_otp(context: ContextTypes.DEFAULT_TYPE, user_id: int, id_num: str, phone_num: str):
    for _ in range(30):  # Check every 6 seconds for 3 minutes
        await asyncio.sleep(6)
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🔔 **নতুন SMS/OTP এসেছে!**\n\n📱 **নম্বর:** `{phone_num}`\n🔑 **OTP Code:** `{otp}`",
                    parse_mode="Markdown"
                )
                set_number_status(id_num, "end") # Mark completed
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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))

    print("VAK-SMS Bot fully initialized...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
