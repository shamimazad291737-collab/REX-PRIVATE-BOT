import os
import requests
import asyncio
from datetime import datetime, timedelta
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MONGO_URI = os.getenv("MONGO_URI", "YOUR_MONGO_URI_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# API Setup
API_KEY = os.getenv("5SIM_API_KEY", "YOUR_5SIM_API_KEY_HERE")
API_URL = "https://5sim.net/v1/user"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
}

# Database Setup
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["telegram_otp_bot"]
users_col = db["users"]
settings_col = db["settings"]

# Global State for active orders
active_orders = {}

# ==================== DATABASE HELPERS ====================
def get_bot_settings():
    settings = settings_col.find_one({"_id": "bot_settings"})
    if not settings:
        default_settings = {
            "_id": "bot_settings",
            "is_active": True,
            "wa_rate": 0.50
        }
        settings_col.insert_one(default_settings)
        return default_settings
    return settings

def is_bot_active():
    return get_bot_settings().get("is_active", True)

def set_bot_active(status: bool):
    settings_col.update_one({"_id": "bot_settings"}, {"$set": {"is_active": status}}, upsert=True)

def get_wa_rate():
    return get_bot_settings().get("wa_rate", 0.50)

def set_wa_rate(rate: float):
    settings_col.update_one({"_id": "bot_settings"}, {"$set": {"wa_rate": rate}}, upsert=True)

def get_or_create_user(user):
    u = users_col.find_one({"user_id": user.id})
    if not u:
        u = {
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user.full_name or "User",
            "balance": 0.0,
            "is_banned": False,
            "subscription_expiry": None,
            "otp_count": 0
        }
        users_col.insert_one(u)
    return u

def check_subscription(user_id):
    if user_id == ADMIN_ID:
        return True
    u = users_col.find_one({"user_id": user_id})
    if not u:
        return False
    expiry = u.get("subscription_expiry")
    if expiry and expiry > datetime.now():
        return True
    return False

# ==================== 5SIM API HELPERS ====================
def buy_number(country="indonesia", operator="any", product="whatsapp"):
    url = f"{API_URL}/buy/activation/{country}/{operator}/{product}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return {"error": f"API Error: {response.status_code} - {response.text}"}
    except Exception as e:
        return {"error": str(e)}

def fetch_otp_code(order_id):
    url = f"{API_URL}/check/{order_id}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def set_number_status(order_id, status="bad"):
    url = f"{API_URL}/finish/{order_id}" if status == "finish" else f"{API_URL}/ban/{order_id}"
    try:
        requests.get(url, headers=HEADERS, timeout=10)
    except Exception:
        pass

# ==================== KEYBOARDS ====================
def get_main_keyboard(user_id):
    kb = [
        [InlineKeyboardButton("📱 Buy WhatsApp OTP", callback_data="buy_wa")],
        [InlineKeyboardButton("💳 My Account & Balance", callback_data="my_account"), InlineKeyboardButton("➕ Add Deposit", callback_data="deposit")],
        [InlineKeyboardButton("⭐ Active Subscription", callback_data="buy_subscription")]
    ]
    if user_id == ADMIN_ID:
        kb.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def get_admin_keyboard():
    status_str = "🟢 ON (Active)" if is_bot_active() else "🔴 OFF (Maintenance)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 View All Users", callback_data="admin_view_users")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_start"), InlineKeyboardButton("✅ Unban User", callback_data="admin_unban_start")],
        [InlineKeyboardButton("💵 Set WA Rate", callback_data="admin_rate_start"), InlineKeyboardButton("➕ Add Balance", callback_data="admin_add_bal_start")],
        [InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(f"Bot Status: {status_str}", callback_data="admin_toggle_bot")]
    ])

# ==================== HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = get_or_create_user(user)

    if u.get("is_banned", False):
        await update.message.reply_text("🚫 Apni ei bot theke banned hoyechen.")
        return

    if not is_bot_active() and user.id != ADMIN_ID:
        await update.message.reply_text("🛠️ Bot ekhon maintenance mode-e ache. Doyakor ektu por chesta korun.")
        return

    welcome_text = (
        f"👋 **Hello {user.first_name}!**\n\n"
        f"Welcome to **OTP Store Bot**! Apni ekan theke WhatsApp er OTP shohojey kinte parben.\n\n"
        f"💰 **Apnar Balance:** `${u.get('balance', 0.0):.4f}` USDT\n"
        f"📌 Services select korte nicher menu byabohar korun:"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user.id))

async def process_otp_success(context: ContextTypes.DEFAULT_TYPE, id_num: str, otp: str):
    if id_num in active_orders:
        order_info = active_orders.pop(id_num)
        target_user_id = order_info["user_id"]
        cost = order_info["cost"]

        set_number_status(id_num, "finish")
        users_col.update_one({"user_id": target_user_id}, {"$inc": {"balance": -cost, "otp_count": 1}})

        success_msg = (
            f"🎉 **OTP Received Successfully!**\n\n"
            f"📱 **Number:** `{order_info['phone']}`\n"
            f"🔑 **OTP Code:** `{otp}`\n"
            f"💵 **Charged:** `${cost:.4f}` USDT"
        )
        await context.bot.send_message(chat_id=target_user_id, text=success_msg, parse_mode="Markdown")

async def handle_admin_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or "admin_action" not in context.user_data:
        return False

    action = context.user_data.pop("admin_action")
    text = update.message.text.strip()

    if action == "set_rate":
        try:
            rate = float(text)
            set_wa_rate(rate)
            await update.message.reply_text(f"✅ **WhatsApp Rate successfully updated to `${rate:.4f}` USDT!**", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Sothik number likhun (e.g. 0.50).")

    elif action == "add_balance":
        try:
            parts = text.split()
            target_uid = int(parts[0])
            amount = float(parts[1])
            users_col.update_one({"user_id": target_uid}, {"$inc": {"balance": amount}})
            await update.message.reply_text(f"✅ User `{target_uid}`-er balance-e `${amount}` USDT jukto kora hoyeche.", parse_mode="Markdown")
            await context.bot.send_message(chat_id=target_uid, text=f"🎉 Admin apnar balance-e `${amount}` USDT jukto korechen!")
        except Exception:
            await update.message.reply_text("❌ Format vul! Sothik format: `User_ID Amount` (e.g. `123456789 5.0`)", parse_mode="Markdown")

    elif action == "ban_user":
        try:
            target_uid = int(text)
            users_col.update_one({"user_id": target_uid}, {"$set": {"is_banned": True}})
            await update.message.reply_text(f"🚫 User `{target_uid}`-ke ban kora hoyeche.", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Sothik User ID likhun.")

    elif action == "unban_user":
        try:
            target_uid = int(text)
            users_col.update_one({"user_id": target_uid}, {"$set": {"is_banned": False}})
            await update.message.reply_text(f"✅ User `{target_uid}`-ke unban kora hoyeche.", parse_mode="Markdown")
        except ValueError:
            await update.message.reply_text("❌ Sothik User ID likhun.")

    elif action == "broadcast":
        all_users = users_col.find({})
        count = 0
        for u in all_users:
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=f"📢 **Announcement:**\n\n{text}", parse_mode="Markdown")
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await update.message.reply_text(f"✅ Broadcast complete! Received: {count} users.")

    return True

async def handle_photo_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    caption = update.message.caption or ""

    if caption.startswith("/deposit"):
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_dep_{user_id}_{caption.split()[-1] if len(caption.split())>1 else '0'}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_dep_{user_id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=f"💳 **New Deposit Request!**\n\n👤 User: `{user_id}`\n📝 Caption: {caption}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await update.message.reply_text("📩 Apnar deposit proof admin-er kache pathano hoyeche. Review seshe balance add hobe.")

    elif caption.startswith("/sub"):
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_sub_{user_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_sub_{user_id}")
            ]
        ])
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=update.message.photo[-1].file_id,
            caption=f"⭐ **New Subscription Request!**\n\n👤 User: `{user_id}`\n📝 Caption: {caption}",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await update.message.reply_text("📩 Apnar subscription proof admin-er kache pathano hoyeche. Verification seshe approve hobe.")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_admin_inputs(update, context):
        return

# ==================== CALLBACK QUERY HANDLER ====================
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    u = get_or_create_user(query.from_user)
    if u.get("is_banned", False):
        await query.message.reply_text("🚫 Apni banned.")
        return

    if data == "my_account":
        sub_status = "✅ Active" if check_subscription(user_id) else "❌ Inactive"
        text = (
            f"👤 **Account Overview:**\n\n"
            f"🆔 **User ID:** `{user_id}`\n"
            f"💰 **Balance:** `${u.get('balance', 0.0):.4f}` USDT\n"
            f"⭐ **Subscription:** {sub_status}\n"
            f"📩 **Total OTP Rcv:** `{u.get('otp_count', 0)}`"
        )
        await query.message.reply_text(text, parse_mode="Markdown")

    elif data == "deposit":
        dep_text = (
            "💳 **Deposit Instructions:**\n\n"
            "Apnar balance add korte nicher address-e USDT (TRC20/BEP20) pathan:\n"
            "<code>YOUR_USDT_WALLET_ADDRESS</code>\n\n"
            "📸 Payment korar por screenshot-er caption-e `/deposit <amount>` লিখে ছবিটি এখানে পাঠান।"
        )
        await query.message.reply_text(dep_text, parse_mode="HTML")

    elif data == "buy_subscription":
        sub_text = (
            "⭐ **Buy Subscription Plan:**\n\n"
            "📌 **3 Days Access:** $1.00 USDT\n\n"
            "Payment complete kore screenshot-er caption-e `/sub` লিখে ছবিটি এখানে পাঠান।"
        )
        await query.message.reply_text(sub_text, parse_mode="Markdown")

    elif data == "buy_wa":
        if not check_subscription(user_id):
            await query.message.reply_text("❌ WhatsApp OTP kinte prothome Subscription active korte hobe!")
            return

        rate = get_wa_rate()
        if u.get("balance", 0.0) < rate:
            await query.message.reply_text(f"❌ Apnar porjapto balance nei! Minimum `${rate:.4f}` USDT lagbe.")
            return

        await query.message.reply_text("🔄 Ordering WhatsApp Number...")
        res = buy_number(country="indonesia", operator="any", product="whatsapp")

        if "error" in res:
            await query.message.reply_text(f"❌ Number pawa jayni: {res['error']}")
            return

        id_num = str(res["id"])
        phone = res["phone"]

        active_orders[id_num] = {
            "user_id": user_id,
            "phone": phone,
            "cost": rate
        }

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Check OTP", callback_data=f"check_otp_{id_num}")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"cancel_num_{id_num}")]
        ])

        msg = (
            f"📱 **WhatsApp Number Purchased!**\n\n"
            f"📞 **Number:** `{phone}`\n"
            f"🆔 **Order ID:** `{id_num}`\n\n"
            f"👉 WhatsApp-e number-ti boshian ebong OTP er jonno 'Check OTP' button-e চাপ দিন।"
        )
        await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)

    elif data.startswith("check_otp_"):
        id_num = data.split("_")[2]
        res = fetch_otp_code(id_num)
        if isinstance(res, dict) and "smsCode" in res and res["smsCode"]:
            otp = res["smsCode"]
            await process_otp_success(context, id_num, otp)
        else:
            await query.message.reply_text("⏳ Ekhono OTP asheni, ektu por abar chesta korun.")

    elif data.startswith("cancel_num_"):
        id_num = data.split("_")[2]
        if id_num in active_orders:
            set_number_status(id_num, "bad")
            active_orders.pop(id_num, None)
            await query.edit_message_text(
                f"{query.message.text}\n\n❌ **Number-ti cancel kora hoyeche (Kono balance katini).**",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("❌ Ei order-ti ar active nei ba already OTP ashe geche.")

    # ================= ADMIN CALLBACKS =================
    elif data == "admin_panel" and user_id == ADMIN_ID:
        await query.message.reply_text("⚙️ **Welcome to Admin Panel:**", parse_mode="Markdown", reply_markup=get_admin_keyboard())

    elif data == "admin_view_users" and user_id == ADMIN_ID:
        try:
            now = datetime.now()
            subscribed_users = list(users_col.find({
                "subscription_expiry": {"$gt": now}
            }))
            
            if not subscribed_users:
                await query.message.reply_text("📋 Currently, there are no active subscribed users.")
                return
            
            msg = f"👥 **Active Subscribed Users ({len(subscribed_users)}):**\n\n"
            for u_item in subscribed_users:
                uid = u_item.get("user_id", "N/A")
                raw_name = str(u_item.get("full_name", "User"))
                safe_name = raw_name.replace("*", "").replace("_", "").replace("`", "").replace("[", "").replace("]", "")
                bal = u_item.get("balance", 0.0)
                otp_cnt = u_item.get("otp_count", 0)
                
                msg += f"• **{safe_name}** (`{uid}`)\n  └ 💰 Balance: `${bal:.4f}` USDT | 📩 OTP Rcv: `{otp_cnt}`\n\n"
                
            await query.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.message.reply_text(f"❌ Error loading users: {str(e)}")

    elif data == "admin_toggle_bot" and user_id == ADMIN_ID:
        current_status = is_bot_active()
        new_status = not current_status
        set_bot_active(new_status)
        status_text = "🟢 **Bot ON (Active) kora hoyeche!**" if new_status else "🔴 **Bot OFF (Maintenance Mode) kora hoyeche!**"
        
        try:
            await query.edit_message_reply_markup(reply_markup=get_admin_keyboard())
        except Exception:
            pass
        await query.message.reply_text(status_text, parse_mode="Markdown")

    elif data == "admin_rate_start" and user_id == ADMIN_ID:
        context.user_data["admin_action"] = "set_rate"
        await query.message.reply_text("💵 New WhatsApp Rate type korun (e.g. `0.50`):", parse_mode="Markdown")

    elif data == "admin_add_bal_start" and user_id == ADMIN_ID:
        context.user_data["admin_action"] = "add_balance"
        await query.message.reply_text("➕ User ID & Balance likhun (e.g. `123456789 5.0`):", parse_mode="Markdown")

    elif data == "admin_ban_start" and user_id == ADMIN_ID:
        context.user_data["admin_action"] = "ban_user"
        await query.message.reply_text("🚫 Ban korar jonno User ID type korun:", parse_mode="Markdown")

    elif data == "admin_unban_start" and user_id == ADMIN_ID:
        context.user_data["admin_action"] = "unban_user"
        await query.message.reply_text("✅ Unban korar jonno User ID type korun:", parse_mode="Markdown")

    elif data == "admin_broadcast_start" and user_id == ADMIN_ID:
        context.user_data["admin_action"] = "broadcast"
        await query.message.reply_text("📢 Sabai ke pathano jonno message type korun:", parse_mode="Markdown")

    elif data.startswith("approve_dep_"):
        parts = data.split("_")
        target_id = int(parts[2])
        amount = float(parts[3])
        users_col.update_one({"user_id": target_id}, {"$inc": {"balance": amount}})
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Approved & Balance Added!**")
        await context.bot.send_message(chat_id=target_id, text=f"🎉 **Apnar `${amount}` USDT deposit shofolbhabe jukto kora hoyeche!**", reply_markup=get_main_keyboard(target_id))

    elif data.startswith("reject_dep_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Deposit Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar deposit request-ti batil kora hoyeche.")

    elif data.startswith("approve_sub_"):
        target_id = int(data.split("_")[2])
        expiry_date = datetime.now() + timedelta(days=3)
        users_col.update_one({"user_id": target_id}, {"$set": {"subscription_expiry": expiry_date}})
        await query.edit_message_caption(caption=query.message.caption + "\n\n✅ **Subscription Approved (3 Days Active)!**")
        
        await context.bot.send_message(
            chat_id=target_id,
            text="🎉 **Apnar Subscription Approved hoyeche!** 3 Diner jonno bot-er sob features active kora hoyeche.",
            reply_markup=get_main_keyboard(target_id)
        )

    elif data.startswith("reject_sub_"):
        target_id = int(data.split("_")[2])
        await query.edit_message_caption(caption=query.message.caption + "\n\n❌ **Subscription Rejected!**")
        await context.bot.send_message(chat_id=target_id, text="❌ Apnar subscription request-ti batil kora hoyeche.")

# ==================== MAIN APPLICATION ====================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_messages))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    app.add_handler(CallbackQueryHandler(handle_callbacks))

    print("🚀 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
