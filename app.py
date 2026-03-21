import asyncio
import logging
import os
import json
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InaccessibleMessage,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
from datetime import datetime, timedelta

# ===================== SOZLAMALAR (environment variables) =====================
BOT_TOKEN = os.environ["BOT_TOKEN"]

# Format: '[{"id":"@kanal1","name":"Kanal 1","link":"https://t.me/kanal1"},...]'
REQUIRED_CHANNELS = json.loads(os.environ["REQUIRED_CHANNELS"])

# Mablag' yig'ish kanali
EARNING_CHANNEL_ID = os.environ["EARNING_CHANNEL_ID"]

# Format: "123456789,987654321"
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

# Kundalik bonus miqdori (so'm)
DAILY_BONUS_AMOUNT = int(os.environ.get("DAILY_BONUS_AMOUNT", "500"))

# ===================== DATABASE =====================
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance INTEGER DEFAULT 0,
            last_bonus TEXT,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rewarded_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            order_id INTEGER,
            UNIQUE(user_id, order_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id TEXT,
            channel_link TEXT,
            channel_title TEXT,
            channel_members INTEGER,
            amount INTEGER,
            confirmed INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            message_id INTEGER,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(user_id, username, full_name):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, balance, created_at) VALUES (?,?,?,0,?)",
              (user_id, username, full_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_balance(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def add_balance(user_id, amount):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def subtract_balance(user_id, amount):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()

def get_last_bonus(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT last_bonus FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_last_bonus(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def create_order(user_id, channel_id, channel_link, channel_title, channel_members, amount):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO orders (user_id, channel_id, channel_link, channel_title, channel_members, amount, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (user_id, channel_id, channel_link, channel_title, channel_members, amount, datetime.now().isoformat()))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id

def get_order(order_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_order_message_id(order_id, message_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE orders SET message_id=? WHERE id=?", (message_id, order_id))
    conn.commit()
    conn.close()

def update_order_confirmed(order_id, confirmed):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE orders SET confirmed=? WHERE id=?", (confirmed, order_id))
    conn.commit()
    conn.close()

def has_been_rewarded(user_id, order_id) -> bool:
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT 1 FROM rewarded_users WHERE user_id=? AND order_id=?", (user_id, order_id))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_rewarded(user_id, order_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO rewarded_users (user_id, order_id) VALUES (?,?)", (user_id, order_id))
    conn.commit()
    conn.close()

def complete_order(order_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE orders SET status='completed' WHERE id=?", (order_id,))
    conn.commit()
    conn.close()

def get_active_orders():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE status='active'", )
    rows = c.fetchall()
    conn.close()
    return rows

# ===================== FSM STATES =====================
class OrderState(StatesGroup):
    waiting_channel = State()
    waiting_amount = State()

# ===================== KEYBOARDS =====================
def main_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Mening hisobim"), KeyboardButton(text="💰 Mablag' yig'ish")],
            [KeyboardButton(text="💳 Hisobni to'ldirish"), KeyboardButton(text="🎁 Kundalik bonus")],
            [KeyboardButton(text="📦 Buyurtma berish")],
        ],
        resize_keyboard=True
    )
    return kb

def back_keyboard():
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Orqaga")]],
        resize_keyboard=True
    )
    return kb

def earning_inline():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 Kanal orqali obuna", callback_data="earn_channel"),
            InlineKeyboardButton(text="🤖 Bot orqali obuna", callback_data="earn_bot"),
        ]
    ])
    return kb

def channel_sub_inline():
    buttons = []
    for ch in REQUIRED_CHANNELS:
        buttons.append([InlineKeyboardButton(text=f"✅ {ch['name']}", url=ch['link'])])
    buttons.append([InlineKeyboardButton(text="✔️ Tekshirish", callback_data="check_sub")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return kb

# ===================== BOT & DISPATCHER =====================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ===================== OBUNA TEKSHIRISH =====================
async def check_subscription(user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True

# ===================== /START =====================
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        text = (
            "👋 <b>Assalomu aleykum!</b>\n\n"
            "Bu bot orqali <b>obunachi yig'ishingiz mumkin</b> 🎯\n\n"
            "Botdan foydalanish uchun avval quyidagi kanallarga obuna bo'ling 👇"
        )
        await message.answer(text, reply_markup=channel_sub_inline(), parse_mode="HTML")
    else:
        await message.answer(
            "👋 <b>Assalomu aleykum!</b>\n\nBu bot orqali <b>obunachi yig'ishingiz mumkin</b> 🎯\n\nQuyidagi tugmalardan birini tanlang:",
            reply_markup=main_keyboard(),
            parse_mode="HTML"
        )

# ===================== OBUNA TEKSHIRISH CALLBACK =====================
@router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery):
    subscribed = await check_subscription(call.from_user.id)
    if subscribed:
        await call.message.edit_text("✅ Obuna tasdiqlandi! Botdan foydalanishingiz mumkin.")
        await call.message.answer(
            "Quyidagi tugmalardan birini tanlang:",
            reply_markup=main_keyboard()
        )
    else:
        await call.answer("❌ Siz hali barcha kanallarga obuna bo'lmagansiz!", show_alert=True)

# ===================== ORQAGA =====================
@router.message(F.text == "⬅️ Orqaga")
async def back_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Asosiy menyu:", reply_markup=main_keyboard())

# ===================== MENING HISOBIM =====================
@router.message(F.text == "👤 Mening hisobim")
async def my_account(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        await message.answer("❌ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    balance = get_balance(message.from_user.id)
    subscribers = balance // 1000

    text = (
        f"👤 <b>Mening hisobim</b>\n\n"
        f"💰 Balans: <b>{balance:,} so'm</b>\n"
        f"👥 Obunachi: <b>{subscribers} ta</b>\n\n"
        f"<i>1 000 so'm = 1 ta obunachi</i>"
    )
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

# ===================== MABLAG' YIG'ISH =====================
@router.message(F.text == "💰 Mablag' yig'ish")
async def earn_money(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        await message.answer("❌ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    text = (
        "💰 <b>Mablag' yig'ish</b>\n\n"
        "Quyidagi usullardan biri orqali <b>obuna bo'lib pul ishlang!</b>\n\n"
        "📢 <b>Kanal orqali</b> — boshqalarning kanaliga obuna bo'ling va pul ishlang\n"
        "🤖 <b>Bot orqali</b> — do'stlarni botga taklif qilib pul ishlang"
    )
    await message.answer(text, reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    await message.answer("Usulni tanlang 👇", reply_markup=earning_inline())

# ===================== KANAL ORQALI OBUNA =====================
@router.callback_query(F.data == "earn_channel")
async def earn_via_channel(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)

    # Active orders ko'rsatish
    orders = get_active_orders()
    if not orders:
        await call.message.answer(
            "😔 Hozircha aktiv buyurtmalar yo'q.\n\nKeyinroq qayta urinib ko'ring!",
            reply_markup=back_keyboard()
        )
        return

    text = "📢 <b>Aktiv kanallar</b> — obuna bo'lib pul ishlang!\n\n"
    kb_buttons = []

    for order in orders[:10]:
        order_id, user_id, ch_id, ch_link, ch_title, ch_members, amount, confirmed, status, msg_id, created_at = order
        needed = amount - confirmed
        earn = needed * 1000
        text += (
            f"📌 <b>{ch_title}</b>\n"
            f"👥 Joriy obunachi: {ch_members}\n"
            f"🎯 Kerak: {needed} ta obunachi\n"
            f"💵 Mukofot: 1 obunachi = 1 000 so'm\n"
            f"🔗 Kanal: {ch_link}\n\n"
        )
        kb_buttons.append([
            InlineKeyboardButton(
                text=f"✅ {ch_title} ga obuna bo'ldim",
                callback_data=f"subscribed_to:{order_id}:{ch_id}"
            )
        ])

    kb_buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_main")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    await call.message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)

# ===================== FOYDALANUVCHI OBUNA BO'LGANINI TASDIQLASH =====================
@router.callback_query(F.data.startswith("subscribed_to:"))
async def user_subscribed_to_channel(call: CallbackQuery):
    parts = call.data.split(":")
    order_id = int(parts[1])
    channel_id = parts[2]

    order = get_order(order_id)
    if not order:
        await call.answer("❌ Buyurtma topilmadi!", show_alert=True)
        return

    order_id_db, owner_id, ch_id, ch_link, ch_title, ch_members, amount, confirmed, status, msg_id, created_at = order

    if status != 'active':
        await call.answer("❌ Bu buyurtma allaqachon yakunlangan!", show_alert=True)
        return

    if call.from_user.id == owner_id:
        await call.answer("❌ O'z kanalingizga obuna bo'lib pul ishlash mumkin emas!", show_alert=True)
        return

    # Duplicate tekshirish
    if has_been_rewarded(call.from_user.id, order_id):
        await call.answer("❌ Siz bu buyurtma uchun allaqachon mukofot oldingiz!", show_alert=True)
        return

    # Obuna tekshirish
    try:
        member = await bot.get_chat_member(channel_id, call.from_user.id)
        if member.status in ("left", "kicked", "banned"):
            await call.answer("❌ Siz hali bu kanalga obuna bo'lmagansiz!", show_alert=True)
            return
    except Exception as e:
        await call.answer(f"❌ Xatolik: {str(e)}", show_alert=True)
        return

    # Balans qo'shish
    add_balance(call.from_user.id, 1000)
    mark_rewarded(call.from_user.id, order_id)
    new_confirmed = confirmed + 1
    update_order_confirmed(order_id, new_confirmed)

    await call.answer("✅ Tasdiqlandi! Hisobingizga 1 000 so'm qo'shildi!", show_alert=True)

    # Earning kanalidagi postni yangilash
    if msg_id:
        try:
            new_text = (
                f"📢 <b>{ch_title}</b>\n"
                f"🔗 {ch_link}\n"
                f"👥 Obunachi soni: {ch_members}\n\n"
                f"📊 <b>Buyurtma holati:</b>\n"
                f"🎯 Buyurtma miqdori: {amount}\n"
                f"✅ Tasdiqlangan: {new_confirmed}"
            )
            await bot.edit_message_text(
                chat_id=EARNING_CHANNEL_ID,
                message_id=msg_id,
                text=new_text,
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Buyurtma yakunlandi mi?
    if new_confirmed >= amount:
        complete_order(order_id)
        try:
            await bot.send_message(owner_id, f"🎉 Buyurtmangiz yakunlandi! {amount} ta obunachi to'plandi.")
        except Exception:
            pass

# ===================== BOT ORQALI OBUNA =====================
@router.callback_query(F.data == "earn_bot")
async def earn_via_bot(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{call.from_user.id}"
    text = (
        f"🤖 <b>Bot orqali pul ishlash</b>\n\n"
        f"Do'stlaringizni botga taklif qiling va har bir yangi foydalanuvchi uchun <b>500 so'm</b> ishlang!\n\n"
        f"🔗 Sizning havola:\n<code>{ref_link}</code>"
    )
    await call.message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

# ===================== ORQAGA CALLBACK =====================
@router.callback_query(F.data == "back_main")
async def back_main_callback(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("Asosiy menyu:", reply_markup=main_keyboard())

# ===================== HISOBNI TO'LDIRISH =====================
@router.message(F.text == "💳 Hisobni to'ldirish")
async def top_up(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        await message.answer("❌ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    text = (
        "💳 <b>Hisobni to'ldirish</b>\n\n"
        "Hozircha to'ldirish usullari qo'shilmagan.\n"
        "Admin bilan bog'laning: @admin_username"
    )
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")

# ===================== KUNDALIK BONUS =====================
@router.message(F.text == "🎁 Kundalik bonus")
async def daily_bonus(message: Message):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        await message.answer("❌ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    last_bonus = get_last_bonus(message.from_user.id)
    now = datetime.now()

    if last_bonus:
        last_dt = datetime.fromisoformat(last_bonus)
        next_bonus = last_dt + timedelta(hours=24)
        if now < next_bonus:
            remaining = next_bonus - now
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            await message.answer(
                f"⏳ Keyingi bonus <b>{hours}s {minutes}d</b> dan keyin olishingiz mumkin.",
                reply_markup=back_keyboard(),
                parse_mode="HTML"
            )
            return

    add_balance(message.from_user.id, DAILY_BONUS_AMOUNT)
    set_last_bonus(message.from_user.id)
    balance = get_balance(message.from_user.id)

    await message.answer(
        f"🎁 <b>Kundalik bonus!</b>\n\n"
        f"✅ Hisobingizga <b>{DAILY_BONUS_AMOUNT} so'm</b> qo'shildi!\n"
        f"💰 Joriy balans: <b>{balance:,} so'm</b>",
        reply_markup=back_keyboard(),
        parse_mode="HTML"
    )

# ===================== BUYURTMA BERISH =====================
@router.message(F.text == "📦 Buyurtma berish")
async def place_order(message: Message, state: FSMContext):
    subscribed = await check_subscription(message.from_user.id)
    if not subscribed:
        await message.answer("❌ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    balance = get_balance(message.from_user.id)
    if balance < 1000:
        await message.answer(
            f"❌ Hisobingizda yetarli mablag' yo'q!\n\n"
            f"💰 Joriy balans: <b>{balance} so'm</b>\n"
            f"Minimum buyurtma: <b>1 000 so'm</b> (1 ta obunachi)",
            reply_markup=back_keyboard(),
            parse_mode="HTML"
        )
        return

    text = (
        "📦 <b>Buyurtma berish</b>\n\n"
        "Botni kanalingizga <b>admin</b> qilib qo'ying.\n\n"
        "Keyin kanalingizning username ini yuboring:\n"
        "Masalan: <code>@mening_kanalim</code>"
    )
    await message.answer(text, reply_markup=back_keyboard(), parse_mode="HTML")
    await state.set_state(OrderState.waiting_channel)

# ===================== KANAL USERNAME =====================
@router.message(OrderState.waiting_channel)
async def receive_channel(message: Message, state: FSMContext):
    if message.text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("Asosiy menyu:", reply_markup=main_keyboard())
        return

    channel_input = message.text.strip()
    if not channel_input.startswith("@"):
        channel_input = "@" + channel_input

    try:
        chat = await bot.get_chat(channel_input)
        member_count = await bot.get_chat_member_count(channel_input)

        # Admin tekshirish
        try:
            bot_info = await bot.get_me()
            bot_member = await bot.get_chat_member(channel_input, bot_info.id)
            if bot_member.status not in ("administrator", "creator"):
                await message.answer(
                    f"❌ Botni <b>{chat.title}</b> kanaliga <b>admin</b> qilib qo'ying!\n\n"
                    f"Admin qilgandan keyin qaytadan username yuboring.",
                    parse_mode="HTML"
                )
                return
        except Exception:
            await message.answer(
                f"❌ Botni <b>{chat.title}</b> kanaliga <b>admin</b> qilib qo'ying!\n\n"
                f"Admin qilgandan keyin qaytadan username yuboring.",
                parse_mode="HTML"
            )
            return

        channel_link = f"https://t.me/{chat.username}" if chat.username else "Havola yo'q"

        await state.update_data(
            channel_id=channel_input,
            channel_link=channel_link,
            channel_title=chat.title,
            channel_members=member_count
        )

        balance = get_balance(message.from_user.id)
        max_order = balance // 1000

        text = (
            f"✅ <b>Kanal topildi!</b>\n\n"
            f"📢 Nomi: <b>{chat.title}</b>\n"
            f"🔗 Havola: {channel_link}\n"
            f"👥 Obunachi: <b>{member_count} ta</b>\n\n"
            f"💰 Hisobingiz: <b>{balance:,} so'm</b>\n"
            f"🎯 Maksimal buyurtma: <b>{max_order} ta obunachi</b>\n\n"
            f"Nechta obunachi buyurtma berasiz? (1-{max_order})"
        )
        await message.answer(text, parse_mode="HTML")
        await state.set_state(OrderState.waiting_amount)

    except Exception as e:
        await message.answer(
            f"❌ Kanal topilmadi: <code>{channel_input}</code>\n\n"
            f"Kanal username ni to'g'ri kiriting yoki botni admin qilganingizni tekshiring.",
            parse_mode="HTML"
        )

# ===================== BUYURTMA MIQDORI =====================
@router.message(OrderState.waiting_amount)
async def receive_amount(message: Message, state: FSMContext):
    if message.text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("Asosiy menyu:", reply_markup=main_keyboard())
        return

    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Iltimos, faqat raqam kiriting!")
        return

    if amount < 1:
        await message.answer("❌ Minimum 1 ta obunachi buyurtma berish kerak!")
        return

    balance = get_balance(message.from_user.id)
    cost = amount * 1000
    max_order = balance // 1000

    if amount > max_order:
        await message.answer(
            f"❌ Hisobingizda yetarli mablag' yo'q!\n"
            f"💰 Balans: {balance:,} so'm\n"
            f"🎯 Maksimal: {max_order} ta"
        )
        return

    data = await state.get_data()

    # Tasdiqlash tugmasi
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"confirm_order:{amount}"),
            InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_order"),
        ]
    ])

    text = (
        f"📋 <b>Buyurtma ma'lumotlari</b>\n\n"
        f"📢 Kanal: <b>{data['channel_title']}</b>\n"
        f"🔗 Havola: {data['channel_link']}\n"
        f"👥 Hozirgi obunachi: <b>{data['channel_members']} ta</b>\n"
        f"🎯 Buyurtma: <b>{amount} ta obunachi</b>\n"
        f"💰 Narx: <b>{cost:,} so'm</b>\n\n"
        f"Tasdiqlaysizmi?"
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# ===================== BUYURTMA TASDIQLASH =====================
@router.callback_query(F.data.startswith("confirm_order:"))
async def confirm_order(call: CallbackQuery, state: FSMContext):
    amount = int(call.data.split(":")[1])
    data = await state.get_data()

    # FSM data yo'q bo'lsa (sessiya tugagan bo'lsa)
    if not data.get('channel_id'):
        await call.answer("❌ Sessiya tugagan. Qaytadan buyurtma bering.", show_alert=True)
        await state.clear()
        if call.message and not isinstance(call.message, InaccessibleMessage):
            await call.message.edit_reply_markup(reply_markup=None)
            await call.message.answer("Asosiy menyu:", reply_markup=main_keyboard())
        return

    balance = get_balance(call.from_user.id)
    cost = amount * 1000

    if balance < cost:
        await call.answer("❌ Yetarli mablag' yo'q!", show_alert=True)
        return

    subtract_balance(call.from_user.id, cost)

    order_id = create_order(
        call.from_user.id,
        data['channel_id'],
        data['channel_link'],
        data['channel_title'],
        data['channel_members'],
        amount
    )

    # Earning kanaliga post yuborish
    post_text = (
        f"📢 <b>{data['channel_title']}</b>\n"
        f"🔗 {data['channel_link']}\n"
        f"👥 Obunachi soni: {data['channel_members']}\n\n"
        f"📊 <b>Buyurtma holati:</b>\n"
        f"🎯 Buyurtma miqdori: {amount}\n"
        f"✅ Tasdiqlangan: 0"
    )

    try:
        sent = await bot.send_message(EARNING_CHANNEL_ID, post_text, parse_mode="HTML")
        update_order_message_id(order_id, sent.message_id)
    except Exception as e:
        logging.warning(f"Earning kanaliga yuborishda xato: {e}")

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ <b>Buyurtma qabul qilindi!</b>\n\n"
        f"📢 Kanal: <b>{data['channel_title']}</b>\n"
        f"🎯 Buyurtma: <b>{amount} ta obunachi</b>\n"
        f"💰 To'landi: <b>{cost:,} so'm</b>\n\n"
        f"Buyurtmangiz #{order_id} raqami bilan saqlandi.",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )
    await state.clear()

# ===================== BUYURTMA BEKOR QILISH =====================
@router.callback_query(F.data == "cancel_order")
async def cancel_order(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("❌ Buyurtma bekor qilindi.", reply_markup=main_keyboard())

# ===================== MAIN =====================
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
