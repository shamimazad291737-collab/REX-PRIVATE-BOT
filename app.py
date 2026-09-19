import logging
import os
import threading
import asyncio
from datetime import datetime, timedelta
from flask import Flask
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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

# যে গ্রুপে OTP ফরওয়ার্ড হবে তার Chat ID
OTP_GROUP_ID = os.getenv("OTP_GROUP_ID") 

USD_TO_BDT = 125.0

# Render Free Tier Keep-Alive & UptimeRobot Server
flask_app = Flask("")

@flask_app.route("/")
def home():
    return "Bot is Alive & Running on Render!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# In-Memory Databases
users_db = {}
pending_deposits = {}

# Custom Service Prices in BDT
CUSTOM_PRICES_BDT = {
    "tg": {"name": "Telegram ✈️", "price_bdt": 60.0},
    "wa": {"name": "WhatsApp 💬", "price_bdt": 75.0},
    "go": {"name": "Gmail/Google 📧", "price_bdt": 40.0},
    "fb": {"name": "Facebook 📘", "price_bdt": 50.0},
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
    """মেম্বারশিপ এর মেয়াদ (৩ দিন) আছে কিনা তা অটো চেক করে"""
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user_data(user.id)

    if u_data.get("banned", False):
        await update.message.reply_text("❌ আপনাকে বট থেকে ব্যান করা হয়েছে।")
        return

    is_active = check_and_update_membership(user.id)
    balance_bdt = u_data["balance_bdt"]

    if is_active:
        expiry_date = u_data["expiry"].strftime("%Y-%m-%d %H:%M")
        msg = (
            f"👋 **স্বাগতম {user.first_name}!**\n\n"
            f"💳 **ব্যালেন্স:** ৳{balance_bdt:.2f} BDT\n"
            f"⏳ **মেম্বারশিপ মেয়াদ:** {expiry_date}\n\n"
            f"নিচের বাটন থেকে প্রয়োজনীয় অপশন নির্বাচন করুন:"
        )
        keyboard = []
        for code, info in CUSTOM_PRICES_BDT.items():
            keyboard.append([
                InlineKeyboardButton(
                    f"{info['name']} - ৳{info['price_bdt']:.0f} BDT",
                    callback_data=f"buy_{code}",
                )
            ])
        keyboard.append([
            InlineKeyboardButton("💰 Deposit Balance", callback_data="btn_deposit"),
            InlineKeyboardButton("🔄 Refresh Menu", callback_data="btn_refresh")
        ])

        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        msg = (
            f"👋 **স্বাগতম {user.first_name}!**\n\n"
            f"⚠️ **আপনার ৩ দিনের মেম্বারশিপ মেয়াদ শেষ বা সক্রিয় নেই।**\n"
            f"বট ব্যবহার করতে আপনাকে ৩ দিনের মেম্বারশিপ কিনতে হবে।\n\n"
            f"💵 **ফি:** ৳৩০ BDT\n"
            f"💳 **আপনার ব্যালেন্স:** ৳{balance_bdt:.2f} BDT\n\n"
            f"নিচের বাটন ব্যবহার করুন:"
        )
        keyboard = [
            [InlineKeyboardButton("🛒 Buy 3-Days Membership (৳30)", callback_data="btn_buy_3days")],
            [InlineKeyboardButton("💰 Deposit Balance", callback_data="btn_deposit")]
        ]
        await update.message.reply_text(
            msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def button_click_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    u_data = get_user_data(user_id)

    if data == "btn_refresh":
        await start(update, context)

    elif data == "btn_buy_3days":
        fee_bdt = 30.0
        if check_and_update_membership(user_id):
            await query.edit_message_text("আপনার মেম্বারশিপ ইতিমধ্যেই সক্রিয় আছে!")
            return

        if u_data["balance_bdt"] < fee_bdt:
            keyboard = [[InlineKeyboardButton("💰 Deposit Balance Now", callback_data="btn_deposit")]]
            await query.edit_message_text(
                f"❌ পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳৩০, আপনার আছে: ৳{u_data['balance_bdt']:.2f}\n\nপ্রথমে ব্যালেন্স ডিপোজিট করুন:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        u_data["balance_bdt"] -= fee_bdt
        u_data["approved"] = True
        u_data["expiry"] = datetime.now() + timedelta(days=3)

        keyboard = [[InlineKeyboardButton("🚀 Go to Services", callback_data="btn_refresh")]]
        await query.edit_message_text(
            f"🎉 ৩ দিনের মেম্বারশিপ চালু করা হয়েছে!\nমেয়াদ শেষ হবে: {u_data['expiry'].strftime('%Y-%m-%d %H:%M')}\nঅবশিষ্ট ব্যালেন্স: ৳{u_data['balance_bdt']:.2f}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "btn_deposit":
        keyboard = [
            [
                InlineKeyboardButton("bKash (BDT)", callback_data="dep_bkash"),
                InlineKeyboardButton("Binance (USDT)", callback_data="dep_binance"),
            ]
        ]
        await query.edit_message_text(
            "পেমেন্ট মেথড সিলেক্ট করুন:", reply_markup=InlineKeyboardMarkup(keyboard)
        )


# --- DEPOSIT CONVERSATION FLOW ---

async def deposit_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("bKash (BDT)", callback_data="dep_bkash"),
            InlineKeyboardButton("Binance (USDT)", callback_data="dep_binance"),
        ]
    ]
    await update.message.reply_text(
        "পেমেন্ট মেথড বেছে নিন:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_METHOD


async def deposit_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    method = query.data.split("_")[1]
    context.user_data["dep_method"] = method

    if method == "bkash":
        msg = (
            f"📌 **bKash Payment**\n"
            f"নাম্বার: `{ADMIN_BKASH}`\n\n"
            f"টাকা পাঠানোর পর মেসেজে লিখুন:\n"
            f"`[টাকার পরিমাণ] [TrxID]`\n"
            f"উদাহরণ: `100 TRX12345678`"
        )
    else:
        msg = (
            f"📌 **Binance USDT Payment**\n"
            f"Binance ID: `{ADMIN_BINANCE}`\n"
            f"রেট: $1 = ৳125 BDT\n\n"
            f"USDT পাঠানোর পর মেসেজে লিখুন:\n"
            f"`[USDT পরিমাণ] [Order/TxID]`\n"
            f"উদাহরণ: `5 987654321`"
        )

    await query.edit_message_text(msg, parse_mode="Markdown")
    return WAITING_DETAILS


async def deposit_details_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) < 2:
        await update.message.reply_text("ভুল ফরম্যাট! আবার লিখুন। (যেমন: `100 TRX123456`)")
        return WAITING_DETAILS

    try:
        amount = float(text[0])
        trx_id = text[1]
    except ValueError:
        await update.message.reply_text("পরিমাণ সংখ্যায় লিখুন।")
        return WAITING_DETAILS

    context.user_data["dep_amount"] = amount
    context.user_data["dep_trx"] = trx_id

    await update.message.reply_text("এবার পেমেন্টের স্ক্রিনশট (Photo) পাঠান:")
    return WAITING_PROOF


async def deposit_proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    photo_file_id = update.message.photo[-1].file_id

    method = context.user_data.get("dep_method")
    amount = context.user_data.get("dep_amount")
    trx_id = context.user_data.get("dep_trx")

    dep_id = f"{user.id}_{int(datetime.now().timestamp())}"
    pending_deposits[dep_id] = {
        "user_id": user.id,
        "amount": amount,
        "method": method,
        "trx_id": trx_id,
    }

    curr_str = f"${amount} USDT (৳{amount*USD_TO_BDT:.2f})" if method == "binance" else f"৳{amount} BDT"

    keyboard = [
        [
            InlineKeyboardButton("Approve ✅", callback_data=f"dapp_{dep_id}"),
            InlineKeyboardButton("Reject ❌", callback_data=f"drej_{dep_id}"),
        ]
    ]

    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo_file_id,
        caption=(
            f"📥 **নতুন ডিপোজিট রিকোয়েস্ট!**\n\n"
            f"User: {user.first_name} (`{user.id}`)\n"
            f"Method: {method.upper()}\n"
            f"Amount: {curr_str}\n"
            f"Trx/Order ID: `{trx_id}`"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await update.message.reply_text("আপনার স্ক্রিনশট পাঠানো হয়েছে। এডমিন চেক করে ব্যালেন্স যুক্ত করে দেবে।")
    return ConversationHandler.END


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("বাতিল করা হয়েছে।")
    return ConversationHandler.END


async def admin_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    action, dep_id = query.data.split("_", 1)
    if dep_id not in pending_deposits:
        await query.edit_message_caption(caption="এই রিকোয়েস্টটি আর বিদ্যমান নেই।")
        return

    dep_info = pending_deposits[dep_id]
    user_id = dep_info["user_id"]
    method = dep_info["method"]
    amount = dep_info["amount"]

    added_bdt = amount * USD_TO_BDT if method == "binance" else amount
    u_data = get_user_data(user_id)

    if action == "dapp":
        u_data["balance_bdt"] += added_bdt
        del pending_deposits[dep_id]

        await query.edit_message_caption(
            caption=f"✅ ডিপোজিট অনুমোদিত! User `{user_id}`-এর ব্যালেন্সে ৳{added_bdt:.2f} BDT যোগ করা হয়েছে।",
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 আপনার ৳{added_bdt:.2f} BDT ডিপোজিট সফল হয়েছে!\nবর্তমান ব্যালেন্স: ৳{u_data['balance_bdt']:.2f} BDT",
        )
    elif action == "drej":
        del pending_deposits[dep_id]
        await query.edit_message_caption(caption=f"❌ User `{user_id}`-এর ডিপোজিট বাতিল করা হয়েছে।")
        await context.bot.send_message(
            chat_id=user_id,
            text="আপনার ডিপোজিট রিকোয়েস্টটি বাতিল করা হয়েছে।",
        )


# --- ADMIN PANEL & COMMANDS ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    msg = (
        "⚙️ **Admin Management Control**\n\n"
        "নিচের কমান্ডসমূহ ব্যবহার করতে পারেন:\n"
        "• `/users` - ইউজারের তালিকা দেখা\n"
        "• `/setprice CODE BDT` - দাম পরিবর্তন (যেমন: `/setprice tg 50`)\n"
        "• `/ban USER_ID` - ইউজার ব্যান করা\n"
        "• `/unban USER_ID` - ব্যান বাতিল করা\n"
        "• `/deluser USER_ID` - ইউজার রিমুভ করা\n"
        "• `/addbalance USER_ID BDT` - ম্যানুয়ালি ব্যালেন্স দেওয়া"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def set_price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2:
        await update.message.reply_text("ফরম্যাট: `/setprice CODE BDT` (যেমন: `/setprice tg 50`)", parse_mode="Markdown")
        return

    code = context.args[0].lower()
    try:
        new_price = float(context.args[1])
        if code in CUSTOM_PRICES_BDT:
            CUSTOM_PRICES_BDT[code]["price_bdt"] = new_price
            await update.message.reply_text(f"✅ {CUSTOM_PRICES_BDT[code]['name']}-এর দাম পরিবর্তন করে ৳{new_price:.2f} করা হয়েছে।")
        else:
            await update.message.reply_text("ইনভ্যালিড কোড! (tg, wa, go, fb)")
    except ValueError:
        await update.message.reply_text("সঠিক দাম সংখ্যায় লিখুন।")


async def list_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not users_db:
        await update.message.reply_text("কোনো ইউজার ডাটা নেই।")
        return

    msg = "📋 **ইউজার তালিকা:**\n\n"
    for uid, uinfo in users_db.items():
        status = "BANNED 🚫" if uinfo.get("banned") else ("ACTIVE 🟢" if check_and_update_membership(uid) else "EXPIRED 🔴")
        exp_str = uinfo["expiry"].strftime("%d-%m %H:%M") if uinfo.get("expiry") else "None"
        msg += f"• ID: `{uid}` | BDT: ৳{uinfo['balance_bdt']:.1f} | {status} | Exp: {exp_str}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


async def ban_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    try:
        target_id = int(context.args[0])
        u_data = get_user_data(target_id)
        u_data["banned"] = True
        u_data["approved"] = False
        await update.message.reply_text(f"✅ ইউজার `{target_id}` ব্যান করা হয়েছে।", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("সঠিক User ID দিন।")


async def unban_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    try:
        target_id = int(context.args[0])
        u_data = get_user_data(target_id)
        u_data["banned"] = False
        await update.message.reply_text(f"✅ ইউজার `{target_id}`-এর ব্যান তোলা হয়েছে।", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("সঠিক User ID দিন।")


async def del_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or not context.args:
        return
    try:
        target_id = int(context.args[0])
        if target_id in users_db:
            del users_db[target_id]
            await update.message.reply_text(f"🗑️ ইউজার `{target_id}`-এর ডাটা ডিলিট করা হয়েছে।", parse_mode="Markdown")
        else:
            await update.message.reply_text("ইউজার পাওয়া যায়নি।")
    except ValueError:
        await update.message.reply_text("সঠিক User ID দিন।")


async def add_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or len(context.args) < 2:
        return
    try:
        target_id = int(context.args[0])
        amount = float(context.args[1])
        u_data = get_user_data(target_id)
        u_data["balance_bdt"] += amount
        await update.message.reply_text(f"✅ ইউজার `{target_id}`-এর ব্যালেন্সে ৳{amount:.2f} BDT যুক্ত করা হয়েছে।", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("ID ও অ্যামাউন্ট সংখ্যায় লিখুন।")


# --- AUTO OTP CHECK & FORWARDING TO GROUP ---

async def poll_otp_and_forward(context: ContextTypes.DEFAULT_TYPE, id_num: str, phone_num: str, service_name: str, user_id: int):
    """ব্যাকগ্রাউন্ডে OTP চেক করবে এবং এসএমএস আসলে সাথে সাথে গ্রুপে পাঠাবে"""
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"
    
    for _ in range(100):
        await asyncio.sleep(6)
        try:
            res = requests.get(url).json()
            if "smsCode" in res and res["smsCode"]:
                otp_code = res["smsCode"]
                
                # ১. ইউজারকে ব্যক্তিগত মেসেজে জানানো
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🔑 **আপনার OTP কোড:** `{otp_code}`\n📱 **নম্বর:** `{phone_num}`",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

                # ২. নির্দিষ্ট টেলিগ্রাম গ্রুপে OTP অটো-ফরওয়ার্ড করা
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
                        logging.error(f"Failed to forward OTP to Group: {err}")
                
                break
        except Exception as e:
            logging.error(f"Error checking OTP background task: {e}")


# --- VAK-SMS SERVICES BUYING ---

async def buy_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not check_and_update_membership(user_id):
        keyboard = [[InlineKeyboardButton("🛒 Buy Membership (৳30)", callback_data="btn_buy_3days")]]
        await query.edit_message_text(
            "আপনার ৩ দিনের মেম্বারশিপ শেষ হয়ে গেছে! আবার ব্যবহার করতে মেম্বারশিপ কিনুন:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    service_code = query.data.split("_")[1]
    service_info = CUSTOM_PRICES_BDT.get(service_code)
    
    if not service_info:
        await query.edit_message_text("ইনভ্যালিড সার্ভিস।")
        return

    custom_price_bdt = service_info["price_bdt"]
    u_data = get_user_data(user_id)

    if u_data["balance_bdt"] < custom_price_bdt:
        keyboard = [[InlineKeyboardButton("💰 Deposit Balance", callback_data="btn_deposit")]]
        await query.edit_message_text(
            f"পর্যাপ্ত ব্যালেন্স নেই!\nপ্রয়োজন: ৳{custom_price_bdt:.2f} BDT\nবর্তমান ব্যালেন্স: ৳{u_data['balance_bdt']:.2f} BDT",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    url = f"https://vak-sms.com/api/getNumber/?apiKey={VAK_SMS_API_KEY}&service={service_code}&country=ru"
    try:
        res = requests.get(url).json()
        if "tel" in res and "idNum" in res:
            u_data["balance_bdt"] -= custom_price_bdt
            phone_num = res["tel"]
            id_num = res["idNum"]

            keyboard = [
                [InlineKeyboardButton("📩 Check OTP / SMS", callback_data=f"getotp_{id_num}")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_refresh")]
            ]

            await query.edit_message_text(
                f"✅ **নম্বর নেওয়া সফল হয়েছে!**\n\n"
                f"সার্ভিস: {service_info['name']}\n"
                f"কাটা হয়েছে: ৳{custom_price_bdt:.2f} BDT\n"
                f"অবশিষ্ট ব্যালেন্স: ৳{u_data['balance_bdt']:.2f} BDT\n\n"
                f"📱 **নম্বর:** `{phone_num}`\n"
                f"🆔 **ID Num:** `{id_num}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            asyncio.create_task(
                poll_otp_and_forward(context, id_num, phone_num, service_info['name'], user_id)
            )

        else:
            await query.edit_message_text(f"নম্বর পাওয়া যায়নি: {res.get('error', 'অজানা সমস্যা')}")
    except Exception as e:
        await query.edit_message_text(f"API এরর: {str(e)}")


async def check_otp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    id_num = query.data.split("_")[1]
    url = f"https://vak-sms.com/api/getSmsCode/?apiKey={VAK_SMS_API_KEY}&idNum={id_num}"

    try:
        res = requests.get(url).json()
        if "smsCode" in res and res["smsCode"]:
            keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_refresh")]]
            await query.edit_message_text(
                f"🔑 **আপনার OTP কোড:** `{res['smsCode']}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔄 Check OTP Again", callback_data=f"getotp_{id_num}")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="btn_refresh")]
            ]
            await query.edit_message_text(
                "এখনো কোনো SMS আসেনি। নাম্বারে কোড পাঠিয়ে কিছুক্ষণ পর আবার চেক করুন।",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        await query.edit_message_text(f"এরর: {str(e)}")


def main():
    # 1. Background-এ Flask Server চালানো
    threading.Thread(target=run_flask, daemon=True).start()

    # 2. Asyncio Loop হ্যান্ডেল করা (Python 3.12+ / Render Crash সমাধান)
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(BOT_TOKEN).build()

    deposit_handler = ConversationHandler(
        entry_points=[CommandHandler("deposit", deposit_start_cmd)],
        states={
            WAITING_METHOD: [CallbackQueryHandler(deposit_method_selected, pattern="^dep_")],
            WAITING_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_details_received)],
            WAITING_PROOF: [MessageHandler(filters.PHOTO, deposit_proof_received)],
        },
        fallbacks=[CommandHandler("cancel", deposit_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("users", list_users_cmd))
    app.add_handler(CommandHandler("setprice", set_price_cmd))
    app.add_handler(CommandHandler("ban", ban_user_cmd))
    app.add_handler(CommandHandler("unban", unban_user_cmd))
    app.add_handler(CommandHandler("deluser", del_user_cmd))
    app.add_handler(CommandHandler("addbalance", add_balance_cmd))
    app.add_handler(deposit_handler)

    app.add_handler(CallbackQueryHandler(admin_deposit_callback, pattern="^(dapp|drej)_"))
    app.add_handler(CallbackQueryHandler(button_click_handler, pattern="^btn_"))
    app.add_handler(CallbackQueryHandler(buy_service_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(check_otp_callback, pattern="^getotp_"))

    print("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
