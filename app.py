import asyncio
import logging
import os
import json
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
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

# ══════════════════════════════════════════════
#              SOZLAMALAR
# ══════════════════════════════════════════════
BOT_TOKEN = None
REQUIRED_CHANNELS = []
EARNING_CHANNEL_ID = None
ADMIN_IDS = []
DAILY_BONUS_AMOUNT = 500
CARD_NUMBER = "0000 0000 0000 0000"
CARD_HOLDER = "ISM FAMILIYA"

def load_config():
    global BOT_TOKEN, REQUIRED_CHANNELS, EARNING_CHANNEL_ID
    global ADMIN_IDS, DAILY_BONUS_AMOUNT, CARD_NUMBER, CARD_HOLDER
    BOT_TOKEN       = os.environ["BOT_TOKEN"]
    REQUIRED_CHANNELS = json.loads(os.environ["REQUIRED_CHANNELS"])
    EARNING_CHANNEL_ID = os.environ["EARNING_CHANNEL_ID"]
    ADMIN_IDS       = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
    DAILY_BONUS_AMOUNT = int(os.environ.get("DAILY_BONUS_AMOUNT", "500"))
    CARD_NUMBER     = os.environ.get("CARD_NUMBER", "0000 0000 0000 0000")
    CARD_HOLDER     = os.environ.get("CARD_HOLDER", "ISM FAMILIYA")

# ══════════════════════════════════════════════
#              DATABASE
# ══════════════════════════════════════════════
def db():
    return sqlite3.connect("bot.db")

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            balance     INTEGER DEFAULT 0,
            last_bonus  TEXT,
            referred_by INTEGER DEFAULT NULL,
            ref_paid    INTEGER DEFAULT 0,
            created_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rewarded_users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER,
            order_id INTEGER,
            UNIQUE(user_id, order_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER,
            channel_id     TEXT,
            channel_link   TEXT,
            channel_title  TEXT,
            channel_members INTEGER,
            amount         INTEGER,
            confirmed      INTEGER DEFAULT 0,
            status         TEXT DEFAULT 'active',
            message_id     INTEGER,
            created_at     TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            username     TEXT,
            full_name    TEXT,
            admin_msg_id INTEGER,
            status       TEXT DEFAULT 'pending',
            amount       INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)
    # Mavjud DB ga yangi ustunlar qo'shish (migration)
    for col in [("referred_by", "INTEGER DEFAULT NULL"), ("ref_paid", "INTEGER DEFAULT 0")]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col[0]} {col[1]}")
        except Exception:
            pass
    conn.commit()
    conn.close()

# ── users ──
def create_user(user_id, username, full_name, referred_by=None):
    conn = db(); c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (user_id,username,full_name,balance,referred_by,created_at) VALUES (?,?,?,0,?,?)",
        (user_id, username, full_name, referred_by, datetime.now().isoformat())
    )
    conn.commit(); conn.close()

def get_referrer(user_id):
    """Foydalanuvchini kim taklif qilganini qaytaradi (ref_paid=0 bo'lsa)"""
    conn = db(); c = conn.cursor()
    c.execute("SELECT referred_by, ref_paid FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    if row and row[0] and row[1] == 0:
        return row[0]
    return None

def mark_ref_paid(user_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE users SET ref_paid=1 WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def get_balance(user_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    return row[0] if row else 0

def add_balance(user_id, amount):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def subtract_balance(user_id, amount):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, user_id))
    conn.commit(); conn.close()

def get_last_bonus(user_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT last_bonus FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    return row[0] if row else None

def set_last_bonus(user_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE users SET last_bonus=? WHERE user_id=?", (datetime.now().isoformat(), user_id))
    conn.commit(); conn.close()

def count_users():
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users"); row = c.fetchone(); conn.close()
    return row[0] if row else 0

# ── orders ──
def create_order(user_id, channel_id, channel_link, channel_title, channel_members, amount):
    conn = db(); c = conn.cursor()
    c.execute("""INSERT INTO orders
        (user_id,channel_id,channel_link,channel_title,channel_members,amount,created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (user_id, channel_id, channel_link, channel_title, channel_members, amount, datetime.now().isoformat()))
    oid = c.lastrowid; conn.commit(); conn.close()
    return oid

def get_order(order_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE id=?", (order_id,))
    row = c.fetchone(); conn.close(); return row

def update_order_message_id(order_id, message_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE orders SET message_id=? WHERE id=?", (message_id, order_id))
    conn.commit(); conn.close()

def update_order_confirmed(order_id, confirmed):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE orders SET confirmed=? WHERE id=?", (confirmed, order_id))
    conn.commit(); conn.close()

def complete_order(order_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE orders SET status='completed' WHERE id=?", (order_id,))
    conn.commit(); conn.close()

def get_active_orders():
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM orders WHERE status='active'")
    rows = c.fetchall(); conn.close(); return rows

def count_orders():
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM orders"); row = c.fetchone(); conn.close()
    return row[0] if row else 0

# ── rewarded ──
def has_been_rewarded(user_id, order_id) -> bool:
    conn = db(); c = conn.cursor()
    c.execute("SELECT 1 FROM rewarded_users WHERE user_id=? AND order_id=?", (user_id, order_id))
    result = c.fetchone(); conn.close(); return result is not None

def mark_rewarded(user_id, order_id):
    conn = db(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO rewarded_users (user_id,order_id) VALUES (?,?)", (user_id, order_id))
    conn.commit(); conn.close()

# ── payments ──
def create_payment(user_id, username, full_name):
    conn = db(); c = conn.cursor()
    c.execute("INSERT INTO payments (user_id,username,full_name,created_at) VALUES (?,?,?,?)",
              (user_id, username, full_name, datetime.now().isoformat()))
    pid = c.lastrowid; conn.commit(); conn.close(); return pid

def set_payment_admin_msg(payment_id, admin_msg_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE payments SET admin_msg_id=? WHERE id=?", (admin_msg_id, payment_id))
    conn.commit(); conn.close()

def get_payment(payment_id):
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
    row = c.fetchone(); conn.close(); return row

def approve_payment(payment_id, amount):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE payments SET status='approved', amount=? WHERE id=?", (amount, payment_id))
    conn.commit(); conn.close()

def reject_payment(payment_id):
    conn = db(); c = conn.cursor()
    c.execute("UPDATE payments SET status='rejected' WHERE id=?", (payment_id,))
    conn.commit(); conn.close()

def count_pending_payments():
    conn = db(); c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
    row = c.fetchone(); conn.close(); return row[0] if row else 0

# ══════════════════════════════════════════════
#              FSM STATES
# ══════════════════════════════════════════════
class OrderState(StatesGroup):
    waiting_channel = State()
    waiting_amount  = State()

class TopUpState(StatesGroup):
    waiting_screenshot = State()

class AdminState(StatesGroup):
    waiting_topup_amount  = State()
    waiting_broadcast_msg = State()

# ══════════════════════════════════════════════
#              KEYBOARDS
# ══════════════════════════════════════════════
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Mening hisobim"),  KeyboardButton(text="💰 Mablag' yig'ish")],
        [KeyboardButton(text="💳 Hisobni to'ldirish"), KeyboardButton(text="🎁 Kundalik bonus")],
        [KeyboardButton(text="📦 Buyurtma berish")],
    ], resize_keyboard=True)

def back_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Orqaga")]],
        resize_keyboard=True
    )

def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📊 Statistika"),    KeyboardButton(text="📋 Kutayotgan to'lovlar")],
        [KeyboardButton(text="📣 Xabar yuborish"), KeyboardButton(text="✅ Aktiv buyurtmalar")],
        [KeyboardButton(text="🔙 Asosiy menyu")],
    ], resize_keyboard=True)

def earning_inline():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Kanal orqali", callback_data="earn_channel"),
        InlineKeyboardButton(text="🤖 Bot orqali",   callback_data="earn_bot"),
    ]])

def channel_sub_inline():
    buttons = [[InlineKeyboardButton(text=f"➕ {ch['name']}", url=ch['link'])]
               for ch in REQUIRED_CHANNELS]
    buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def payment_admin_inline(payment_id):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",    callback_data=f"pay_approve:{payment_id}"),
        InlineKeyboardButton(text="❌ Bekor qilish",  callback_data=f"pay_reject:{payment_id}"),
    ]])

# ══════════════════════════════════════════════
#              BOT & DISPATCHER
# ══════════════════════════════════════════════
bot: Bot = None  # type: ignore
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ══════════════════════════════════════════════
#              HELPERS
# ══════════════════════════════════════════════
async def check_subscription(user_id: int) -> bool:
    for ch in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ══════════════════════════════════════════════
#              /START
# ══════════════════════════════════════════════
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    assert message.from_user
    await state.clear()

    args = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else ""

    # Kanal orqali tasdiqlash: /start order_123
    if args.startswith("order_"):
        create_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        await handle_order_confirm(message, args)
        return

    # Referal: /start ref_123456789
    referred_by = None
    if args.startswith("ref_"):
        try:
            ref_id = int(args.split("_")[1])
            if ref_id != message.from_user.id:
                referred_by = ref_id
        except (IndexError, ValueError):
            pass

    create_user(message.from_user.id, message.from_user.username, message.from_user.full_name, referred_by)

    if not await check_subscription(message.from_user.id):
        await message.answer(
            "╔══════════════════╗\n"
            "║  🌟 XUSH KELIBSIZ  ║\n"
            "╚══════════════════╝\n\n"
            "👋 <b>Assalomu aleykum!</b>\n\n"
            "🎯 Bu bot orqali siz:\n"
            "  • Obunachi yig'ishingiz\n"
            "  • Pul ishlashingiz mumkin!\n\n"
            "⚠️ Botdan foydalanish uchun\n"
            "quyidagi kanallarga obuna bo'ling 👇",
            reply_markup=channel_sub_inline(), parse_mode="HTML"
        )
    else:
        name = message.from_user.first_name
        await message.answer(
            f"╔══════════════════╗\n"
            f"║  🌟 XUSH KELIBSIZ  ║\n"
            f"╚══════════════════╝\n\n"
            f"👋 Salom, <b>{name}</b>!\n\n"
            f"🤖 Bu bot orqali <b>obunachi yig'ing</b>\n"
            f"va <b>pul ishlang!</b>\n\n"
            f"📌 Quyidan kerakli bo'limni tanlang 👇",
            reply_markup=main_kb(), parse_mode="HTML"
        )

async def handle_order_confirm(message: Message, args: str):
    assert message.from_user
    try:
        order_id = int(args.split("_")[1])
    except (IndexError, ValueError):
        await message.answer("❌ Noto'g'ri havola.", reply_markup=main_kb())
        return

    order = get_order(order_id)
    if not order:
        await message.answer("❌ Buyurtma topilmadi!", reply_markup=main_kb())
        return

    oid, owner_id, ch_id, ch_link, ch_title, ch_members, amount, confirmed, status, msg_id, created_at = order

    if status != 'active':
        await message.answer(
            "⚠️ Bu buyurtma allaqachon yakunlangan!",
            reply_markup=main_kb()
        )
        return

    if message.from_user.id == owner_id:
        await message.answer(
            "❌ O'z kanalingizga obuna bo'lib pul ishlash mumkin emas!",
            reply_markup=main_kb()
        )
        return

    if has_been_rewarded(message.from_user.id, order_id):
        await message.answer(
            "⚠️ Siz bu buyurtmadan allaqachon mukofot oldingiz!",
            reply_markup=main_kb()
        )
        return

    # Obuna tekshirish
    try:
        member = await bot.get_chat_member(ch_id, message.from_user.id)
        if member.status in ("left", "kicked", "banned"):
            confirm_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=f"➕ {ch_title} ga obuna bo'l", url=ch_link),
            ]])
            await message.answer(
                f"❌ <b>Siz hali kanalga obuna bo'lmagansiz!</b>\n\n"
                f"📢 Kanal: <b>{ch_title}</b>\n\n"
                f"Avval kanalga obuna bo'ling, keyin\n"
                f"yana ushbu havolaga bosing.",
                reply_markup=confirm_kb, parse_mode="HTML"
            )
            return
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}", reply_markup=main_kb())
        return

    # Muvaffaqiyatli
    add_balance(message.from_user.id, 1000)
    mark_rewarded(message.from_user.id, order_id)
    new_confirmed = confirmed + 1
    update_order_confirmed(order_id, new_confirmed)

    balance = get_balance(message.from_user.id)
    await message.answer(
        f"✅ <b>Tasdiqlandi!</b>\n\n"
        f"📢 Kanal: <b>{ch_title}</b>\n\n"
        f"💰 Hisobingizga <b>1 000 so'm</b> qo'shildi!\n"
        f"💳 Joriy balans: <b>{balance:,} so'm</b>",
        reply_markup=main_kb(), parse_mode="HTML"
    )

    # Kanal postini yangilash
    if msg_id:
        try:
            bot_info = await bot.get_me()
            post_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Obuna bo'ldim — 1 000 so'm ol!",
                    url=f"https://t.me/{bot_info.username}?start=order_{order_id}"
                )
            ]])
            await bot.edit_message_text(
                chat_id=str(EARNING_CHANNEL_ID),
                message_id=msg_id,
                text=(
                    f"📢 <b>{ch_title}</b>\n"
                    f"🔗 {ch_link}\n"
                    f"👥 Joriy obunachi: {ch_members}\n\n"
                    f"📊 <b>Buyurtma holati:</b>\n"
                    f"🎯 Buyurtma miqdori: {amount}\n"
                    f"✅ Tasdiqlangan: {new_confirmed}"
                ),
                reply_markup=post_kb,
                parse_mode="HTML"
            )
        except Exception:
            pass

    if new_confirmed >= amount:
        complete_order(order_id)
        try:
            await bot.send_message(
                owner_id,
                f"🎉 <b>Buyurtmangiz yakunlandi!</b>\n\n"
                f"✅ {amount} ta obunachi to'plandi.\n"
                f"📢 Kanal: {ch_title}",
                parse_mode="HTML"
            )
        except Exception:
            pass

# ══════════════════════════════════════════════
#              OBUNA TEKSHIRISH
# ══════════════════════════════════════════════
@router.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery):
    assert call.message and not isinstance(call.message, InaccessibleMessage)
    if await check_subscription(call.from_user.id):
        # Referal bonus — faqat bir marta, to'liq obuna bo'lgandan keyin
        referrer_id = get_referrer(call.from_user.id)
        if referrer_id:
            add_balance(referrer_id, 2000)
            mark_ref_paid(call.from_user.id)
            try:
                ref_balance = get_balance(referrer_id)
                await bot.send_message(
                    referrer_id,
                    f"🎉 <b>Referal bonus!</b>\n\n"
                    f"👤 Taklif qilgan odamingiz botga to'liq qo'shildi!\n"
                    f"💰 Hisobingizga <b>2 000 so'm</b> qo'shildi!\n"
                    f"💳 Joriy balans: <b>{ref_balance:,} so'm</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        await call.message.delete()
        name = call.from_user.first_name
        await call.message.answer(
            f"╔══════════════════╗\n"
            f"║  🌟 XUSH KELIBSIZ  ║\n"
            f"╚══════════════════╝\n\n"
            f"👋 Salom, <b>{name}</b>!\n\n"
            f"🤖 Bu bot orqali <b>obunachi yig'ing</b>\n"
            f"va <b>pul ishlang!</b>\n\n"
            f"📌 Quyidan kerakli bo'limni tanlang 👇",
            reply_markup=main_kb(), parse_mode="HTML"
        )
    else:
        await call.answer("❌ Barcha kanallarga obuna bo'lmagansiz!", show_alert=True)

# ══════════════════════════════════════════════
#              ORQAGA
# ══════════════════════════════════════════════
@router.message(F.text == "⬅️ Orqaga")
async def back_handler(message: Message, state: FSMContext):
    assert message.from_user
    await state.clear()
    await message.answer("📌 Asosiy menyu:", reply_markup=main_kb())

# ══════════════════════════════════════════════
#              MENING HISOBIM
# ══════════════════════════════════════════════
@router.message(F.text == "👤 Mening hisobim")
async def my_account(message: Message):
    assert message.from_user
    if not await check_subscription(message.from_user.id):
        await message.answer("⚠️ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    balance = get_balance(message.from_user.id)
    subscribers = balance // 1000
    name = message.from_user.first_name
    username = f"@{message.from_user.username}" if message.from_user.username else "—"

    await message.answer(
        f"┌─────────────────────┐\n"
        f"│    👤 MENING HISOBIM    │\n"
        f"└─────────────────────┘\n\n"
        f"👤 Ism:       <b>{name}</b>\n"
        f"🔗 Username:  <b>{username}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balans:    <b>{balance:,} so'm</b>\n"
        f"👥 Obunachi:  <b>{subscribers} ta</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💡 <i>1 000 so'm = 1 ta obunachi</i>",
        reply_markup=back_kb(), parse_mode="HTML"
    )

# ══════════════════════════════════════════════
#              MABLAG' YIG'ISH
# ══════════════════════════════════════════════
@router.message(F.text == "💰 Mablag' yig'ish")
async def earn_money(message: Message):
    assert message.from_user
    if not await check_subscription(message.from_user.id):
        await message.answer("⚠️ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    await message.answer(
        "┌─────────────────────┐\n"
        "│   💰 MABLAG' YIG'ISH    │\n"
        "└─────────────────────┘\n\n"
        "Quyidagi usullardan birini tanlang:\n\n"
        "📢 <b>Kanal orqali</b>\n"
        "   Boshqa kanalga obuna bo'lib\n"
        "   <b>1 000 so'm</b> ishlang\n\n"
        "🤖 <b>Bot orqali</b>\n"
        "   Do'stlarni taklif qilib\n"
        "   <b>500 so'm</b> ishlang\n\n"
        "👇 Usulni tanlang:",
        reply_markup=ReplyKeyboardRemove(), parse_mode="HTML"
    )
    await message.answer("⬇️", reply_markup=earning_inline())

# ══════════════════════════════════════════════
#              KANAL ORQALI OBUNA (LIST)
# ══════════════════════════════════════════════
@router.callback_query(F.data == "earn_channel")
async def earn_via_channel(call: CallbackQuery):
    assert call.message and not isinstance(call.message, InaccessibleMessage)
    await call.message.edit_reply_markup(reply_markup=None)
    orders = get_active_orders()

    if not orders:
        await call.message.answer(
            "😔 <b>Hozircha aktiv buyurtmalar yo'q</b>\n\n"
            "Keyinroq qayta urinib ko'ring!",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        return

    text = (
        "┌─────────────────────┐\n"
        "│  📢 AKTIV KANALLAR    │\n"
        "└─────────────────────┘\n\n"
        "Obuna bo'lib <b>1 000 so'm</b> ishlang! 💵\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    kb_buttons = []

    for order in orders[:10]:
        oid, uid, ch_id, ch_link, ch_title, ch_members, amount, confirmed, status, msg_id, created_at = order
        needed = amount - confirmed
        text += (
            f"📌 <b>{ch_title}</b>\n"
            f"   👥 Obunachi: {ch_members} ta\n"
            f"   🎯 Kerak: {needed} ta\n"
            f"   🔗 {ch_link}\n\n"
        )
        kb_buttons.append([InlineKeyboardButton(
            text=f"✅ {ch_title} ga obuna bo'ldim",
            callback_data=f"subscribed_to:{oid}:{ch_id}"
        )])

    kb_buttons.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_main")])
    await call.message.answer(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons),
        parse_mode="HTML", disable_web_page_preview=True
    )

# ══════════════════════════════════════════════
#              OBUNA TASDIQLASH (foydalanuvchi)
# ══════════════════════════════════════════════
@router.callback_query(F.data.startswith("subscribed_to:"))
async def user_subscribed(call: CallbackQuery):
    assert call.data
    parts    = call.data.split(":")
    order_id = int(parts[1])
    channel_id = parts[2]

    order = get_order(order_id)
    if not order:
        await call.answer("❌ Buyurtma topilmadi!", show_alert=True); return

    oid, owner_id, ch_id, ch_link, ch_title, ch_members, amount, confirmed, status, msg_id, created_at = order

    if status != 'active':
        await call.answer("❌ Bu buyurtma yakunlangan!", show_alert=True); return
    if call.from_user.id == owner_id:
        await call.answer("❌ O'z kanalingizga obuna bo'lib pul ishlash mumkin emas!", show_alert=True); return
    if has_been_rewarded(call.from_user.id, order_id):
        await call.answer("❌ Siz bu buyurtmadan allaqachon mukofot oldingiz!", show_alert=True); return

    try:
        member = await bot.get_chat_member(channel_id, call.from_user.id)
        if member.status in ("left", "kicked", "banned"):
            await call.answer("❌ Avval kanalga obuna bo'ling!", show_alert=True); return
    except Exception as e:
        await call.answer(f"❌ Xatolik: {e}", show_alert=True); return

    add_balance(call.from_user.id, 1000)
    mark_rewarded(call.from_user.id, order_id)
    new_confirmed = confirmed + 1
    update_order_confirmed(order_id, new_confirmed)

    await call.answer("✅ Tasdiqlandi! Hisobingizga 1 000 so'm qo'shildi!", show_alert=True)

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=EARNING_CHANNEL_ID, message_id=msg_id,
                text=(
                    f"📢 <b>{ch_title}</b>\n"
                    f"🔗 {ch_link}\n"
                    f"👥 Joriy obunachi: {ch_members}\n\n"
                    f"📊 <b>Holat:</b>\n"
                    f"🎯 Buyurtma miqdori: {amount}\n"
                    f"✅ Tasdiqlangan: {new_confirmed}"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    if new_confirmed >= amount:
        complete_order(order_id)
        try:
            await bot.send_message(owner_id,
                f"🎉 <b>Buyurtmangiz yakunlandi!</b>\n\n"
                f"✅ {amount} ta obunachi to'plandi.\n"
                f"📢 Kanal: {ch_title}",
                parse_mode="HTML"
            )
        except Exception:
            pass

# ══════════════════════════════════════════════
#              BOT ORQALI OBUNA
# ══════════════════════════════════════════════
@router.callback_query(F.data == "earn_bot")
async def earn_via_bot(call: CallbackQuery):
    assert call.message and not isinstance(call.message, InaccessibleMessage)
    await call.message.edit_reply_markup(reply_markup=None)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{call.from_user.id}"
    await call.message.answer(
        "┌─────────────────────┐\n"
        "│  🤖 BOT ORQALI ISHLASH  │\n"
        "└─────────────────────┘\n\n"
        "Do'stlarni botga taklif qiling!\n\n"
        "🎁 Har bir yangi foydalanuvchi uchun\n"
        "   <b>500 so'm</b> ishlaysiz!\n\n"
        f"🔗 Sizning havola:\n<code>{ref_link}</code>\n\n"
        "📤 Havolani do'stlaringizga yuboring!",
        reply_markup=back_kb(), parse_mode="HTML"
    )

# ══════════════════════════════════════════════
#              ORQAGA CALLBACK
# ══════════════════════════════════════════════
@router.callback_query(F.data == "back_main")
async def back_main_cb(call: CallbackQuery):
    assert call.message and not isinstance(call.message, InaccessibleMessage)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("📌 Asosiy menyu:", reply_markup=main_kb())

# ══════════════════════════════════════════════
#              HISOBNI TO'LDIRISH
# ══════════════════════════════════════════════
@router.message(F.text == "💳 Hisobni to'ldirish")
async def top_up(message: Message, state: FSMContext):
    assert message.from_user
    if not await check_subscription(message.from_user.id):
        await message.answer("⚠️ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    await message.answer(
        "┌─────────────────────┐\n"
        "│   💳 HISOBNI TO'LDIRISH  │\n"
        "└─────────────────────┘\n\n"
        "Quyidagi karta raqamiga pul o'tkazing:\n\n"
        f"💳 <b>Karta raqami:</b>\n"
        f"<code>{CARD_NUMBER}</code>\n\n"
        f"👤 <b>Karta egasi:</b>\n"
        f"<b>{CARD_HOLDER}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📸 Pul o'tkazgandan so'ng <b>chek rasmini</b> yuboring.\n"
        "Admin tekshirib, hisobingizni to'ldiradi.\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 Chek rasmini yuboring:",
        reply_markup=back_kb(), parse_mode="HTML"
    )
    await state.set_state(TopUpState.waiting_screenshot)

@router.message(TopUpState.waiting_screenshot)
async def receive_screenshot(message: Message, state: FSMContext):
    assert message.from_user
    if message.text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("📌 Asosiy menyu:", reply_markup=main_kb())
        return

    if not message.photo:
        await message.answer(
            "❌ Iltimos, <b>rasm</b> yuboring!\n\n"
            "📸 To'lov chekining rasmini yuboring.",
            parse_mode="HTML"
        )
        return

    payment_id = create_payment(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or ""
    )

    username = f"@{message.from_user.username}" if message.from_user.username else "yo'q"
    caption = (
        f"💳 <b>YANGI TO'LOV SO'ROVI</b>\n\n"
        f"🆔 To'lov ID:  <b>#{payment_id}</b>\n"
        f"👤 Ism:        <b>{message.from_user.full_name}</b>\n"
        f"🔗 Username:   <b>{username}</b>\n"
        f"🆔 User ID:    <code>{message.from_user.id}</code>\n\n"
        f"💰 Balans:     <b>{get_balance(message.from_user.id):,} so'm</b>"
    )

    for admin_id in ADMIN_IDS:
        try:
            sent = await bot.send_photo(
                chat_id=admin_id,
                photo=message.photo[-1].file_id,
                caption=caption,
                reply_markup=payment_admin_inline(payment_id),
                parse_mode="HTML"
            )
            set_payment_admin_msg(payment_id, sent.message_id)
        except Exception:
            pass

    await state.clear()
    await message.answer(
        "✅ <b>Chek qabul qilindi!</b>\n\n"
        "⏳ Admin tekshirgandan so'ng\n"
        "hisobingiz to'ldiriladi.\n\n"
        "🕐 Odatda 5-30 daqiqa ichida.",
        reply_markup=main_kb(), parse_mode="HTML"
    )

# ── Admin: Tasdiqlash ──
@router.callback_query(F.data.startswith("pay_approve:"))
async def admin_approve_payment(call: CallbackQuery, state: FSMContext):
    assert call.data and call.message and not isinstance(call.message, InaccessibleMessage)
    if not is_admin(call.from_user.id):
        await call.answer("❌ Ruxsat yo'q!", show_alert=True); return

    payment_id = int(call.data.split(":")[1])
    payment = get_payment(payment_id)
    if not payment:
        await call.answer("❌ To'lov topilmadi!", show_alert=True); return

    pid, user_id, username, full_name, admin_msg_id, status, amount, created_at = payment

    if status != 'pending':
        await call.answer("⚠️ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True); return

    await state.update_data(payment_id=payment_id, user_id=user_id, full_name=full_name)
    await state.set_state(AdminState.waiting_topup_amount)

    await call.message.answer(
        f"💰 <b>#{payment_id}</b> to'lov uchun\n"
        f"necha so'm kiritilsin?\n\n"
        f"Foydalanuvchi: <b>{full_name}</b>\n\n"
        f"Raqam kiriting (masalan: <code>50000</code>):",
        parse_mode="HTML"
    )
    await call.answer()

@router.message(AdminState.waiting_topup_amount)
async def admin_enter_amount(message: Message, state: FSMContext):
    assert message.from_user
    if not is_admin(message.from_user.id):
        return

    if not message.text:
        await message.answer("❌ Faqat raqam kiriting!"); return
    try:
        amount = int(message.text.strip().replace(" ", "").replace(",", ""))
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!"); return

    if amount <= 0:
        await message.answer("❌ Miqdor 0 dan katta bo'lishi kerak!"); return

    data = await state.get_data()
    payment_id = data['payment_id']
    user_id    = data['user_id']
    full_name  = data['full_name']

    approve_payment(payment_id, amount)
    add_balance(user_id, amount)
    await state.clear()

    await message.answer(
        f"✅ <b>Tasdiqlandi!</b>\n\n"
        f"👤 Foydalanuvchi: <b>{full_name}</b>\n"
        f"💰 Qo'shildi: <b>{amount:,} so'm</b>",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

    try:
        await bot.send_message(
            user_id,
            f"✅ <b>Hisobingiz to'ldirildi!</b>\n\n"
            f"💰 Qo'shildi: <b>{amount:,} so'm</b>\n"
            f"💳 Joriy balans: <b>{get_balance(user_id):,} so'm</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ── Admin: Bekor qilish ──
@router.callback_query(F.data.startswith("pay_reject:"))
async def admin_reject_payment(call: CallbackQuery):
    assert call.data and call.message and not isinstance(call.message, InaccessibleMessage)
    if not is_admin(call.from_user.id):
        await call.answer("❌ Ruxsat yo'q!", show_alert=True); return

    payment_id = int(call.data.split(":")[1])
    payment = get_payment(payment_id)
    if not payment:
        await call.answer("❌ To'lov topilmadi!", show_alert=True); return

    pid, user_id, username, full_name, admin_msg_id, status, amount, created_at = payment

    if status != 'pending':
        await call.answer("⚠️ Bu to'lov allaqachon ko'rib chiqilgan!", show_alert=True); return

    reject_payment(payment_id)

    await call.message.edit_caption(
        caption=(call.message.caption or "") + "\n\n❌ <b>BEKOR QILINDI</b>",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            user_id,
            "❌ <b>To'lovingiz bekor qilindi.</b>\n\n"
            "Muammo bo'lsa admin bilan bog'laning.",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await call.answer("✅ Bekor qilindi!")

# ══════════════════════════════════════════════
#              KUNDALIK BONUS
# ══════════════════════════════════════════════
@router.message(F.text == "🎁 Kundalik bonus")
async def daily_bonus(message: Message):
    assert message.from_user
    if not await check_subscription(message.from_user.id):
        await message.answer("⚠️ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    last_bonus = get_last_bonus(message.from_user.id)
    now = datetime.now()

    if last_bonus:
        last_dt   = datetime.fromisoformat(last_bonus)
        next_bonus = last_dt + timedelta(hours=24)
        if now < next_bonus:
            remaining = next_bonus - now
            h = int(remaining.total_seconds() // 3600)
            m = int((remaining.total_seconds() % 3600) // 60)
            await message.answer(
                "┌─────────────────────┐\n"
                "│    🎁 KUNDALIK BONUS    │\n"
                "└─────────────────────┘\n\n"
                f"⏳ Keyingi bonus:\n"
                f"   <b>{h} soat {m} daqiqadan keyin</b>\n\n"
                f"💡 Har 24 soatda bir marta olish mumkin!",
                reply_markup=back_kb(), parse_mode="HTML"
            )
            return

    add_balance(message.from_user.id, DAILY_BONUS_AMOUNT)
    set_last_bonus(message.from_user.id)
    balance = get_balance(message.from_user.id)

    await message.answer(
        "┌─────────────────────┐\n"
        "│    🎁 KUNDALIK BONUS    │\n"
        "└─────────────────────┘\n\n"
        f"🎉 Tabriklaymiz!\n\n"
        f"✅ Hisobingizga <b>{DAILY_BONUS_AMOUNT:,} so'm</b> qo'shildi!\n"
        f"💰 Joriy balans: <b>{balance:,} so'm</b>\n\n"
        f"⏰ Keyingi bonus 24 soatdan keyin!",
        reply_markup=back_kb(), parse_mode="HTML"
    )

# ══════════════════════════════════════════════
#              BUYURTMA BERISH
# ══════════════════════════════════════════════
@router.message(F.text == "📦 Buyurtma berish")
async def place_order(message: Message, state: FSMContext):
    assert message.from_user
    if not await check_subscription(message.from_user.id):
        await message.answer("⚠️ Avval kanallarga obuna bo'ling!", reply_markup=channel_sub_inline())
        return

    balance = get_balance(message.from_user.id)
    if balance < 1000:
        await message.answer(
            "┌─────────────────────┐\n"
            "│   📦 BUYURTMA BERISH    │\n"
            "└─────────────────────┘\n\n"
            "❌ <b>Hisobingizda mablag' yetarli emas!</b>\n\n"
            f"💰 Joriy balans: <b>{balance:,} so'm</b>\n"
            f"⚠️ Minimum: <b>1 000 so'm</b>\n\n"
            "💡 Hisobni to'ldiring yoki\n"
            "kundalik bonus oling!",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        return

    await message.answer(
        "┌─────────────────────┐\n"
        "│   📦 BUYURTMA BERISH    │\n"
        "└─────────────────────┘\n\n"
        "📋 <b>Qo'llanma:</b>\n\n"
        "1️⃣ Botni kanalingizga <b>admin</b> qiling\n"
        "2️⃣ Kanal username ini yuboring\n\n"
        "Masalan: <code>@mening_kanalim</code>\n\n"
        "👇 Kanal username ini yuboring:",
        reply_markup=back_kb(), parse_mode="HTML"
    )
    await state.set_state(OrderState.waiting_channel)

@router.message(OrderState.waiting_channel)
async def receive_channel(message: Message, state: FSMContext):
    assert message.from_user
    if message.text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("📌 Asosiy menyu:", reply_markup=main_kb())
        return

    if not message.text:
        return
    channel_input = message.text.strip()
    if not channel_input.startswith("@"):
        channel_input = "@" + channel_input

    try:
        chat = await bot.get_chat(channel_input)
        member_count = await bot.get_chat_member_count(channel_input)

        try:
            bot_info   = await bot.get_me()
            bot_member = await bot.get_chat_member(channel_input, bot_info.id)
            if bot_member.status not in ("administrator", "creator"):
                raise Exception("not admin")
        except Exception:
            await message.answer(
                f"❌ <b>Bot admin emas!</b>\n\n"
                f"📢 Kanal: <b>{chat.title}</b>\n\n"
                f"⚙️ Botni kanalingizga admin qiling,\n"
                f"keyin qaytadan username yuboring.",
                parse_mode="HTML"
            )
            return

        channel_link = f"https://t.me/{chat.username}" if chat.username else "Havola yo'q"
        await state.update_data(
            channel_id=channel_input, channel_link=channel_link,
            channel_title=chat.title, channel_members=member_count
        )

        balance   = get_balance(message.from_user.id)
        max_order = balance // 1000

        await message.answer(
            f"✅ <b>Kanal topildi!</b>\n\n"
            f"┌──────────────────────\n"
            f"│ 📢 {chat.title}\n"
            f"│ 🔗 {channel_link}\n"
            f"│ 👥 {member_count:,} obunachi\n"
            f"└──────────────────────\n\n"
            f"💰 Hisobingiz: <b>{balance:,} so'm</b>\n"
            f"🎯 Maksimal: <b>{max_order} ta obunachi</b>\n\n"
            f"📝 Nechta obunachi buyurtma berasiz?\n"
            f"<i>(1 — {max_order} oraliq)</i>",
            parse_mode="HTML"
        )
        await state.set_state(OrderState.waiting_amount)

    except Exception:
        await message.answer(
            f"❌ <b>Kanal topilmadi!</b>\n\n"
            f"<code>{channel_input}</code>\n\n"
            f"✅ Tekshiring:\n"
            f"  • Username to'g'riligini\n"
            f"  • Bot admin ekanligini",
            parse_mode="HTML"
        )

@router.message(OrderState.waiting_amount)
async def receive_amount(message: Message, state: FSMContext):
    assert message.from_user
    if message.text == "⬅️ Orqaga":
        await state.clear()
        await message.answer("📌 Asosiy menyu:", reply_markup=main_kb())
        return

    if not message.text:
        await message.answer("❌ Faqat raqam kiriting!"); return
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Faqat raqam kiriting!"); return

    if amount < 1:
        await message.answer("❌ Minimum 1 ta obunachi!"); return

    balance   = get_balance(message.from_user.id)
    cost      = amount * 1000
    max_order = balance // 1000

    if amount > max_order:
        await message.answer(
            f"❌ <b>Yetarli mablag' yo'q!</b>\n\n"
            f"💰 Balans: <b>{balance:,} so'm</b>\n"
            f"🎯 Maksimal: <b>{max_order} ta</b>",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",   callback_data=f"confirm_order:{amount}"),
        InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_order"),
    ]])

    await message.answer(
        f"┌─────────────────────┐\n"
        f"│  📋 BUYURTMA MA'LUMOTI  │\n"
        f"└─────────────────────┘\n\n"
        f"📢 Kanal:    <b>{data['channel_title']}</b>\n"
        f"🔗 Havola:   {data['channel_link']}\n"
        f"👥 Obunachi: <b>{data['channel_members']:,} ta</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Buyurtma: <b>{amount} ta obunachi</b>\n"
        f"💰 Narx:     <b>{cost:,} so'm</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Tasdiqlaysizmi?",
        reply_markup=kb, parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("confirm_order:"))
async def confirm_order(call: CallbackQuery, state: FSMContext):
    assert call.data and call.message and not isinstance(call.message, InaccessibleMessage)
    amount = int(call.data.split(":")[1])
    data   = await state.get_data()

    if not data.get('channel_id'):
        await call.answer("❌ Sessiya tugagan. Qaytadan buyurtma bering.", show_alert=True)
        await state.clear()
        if call.message and not isinstance(call.message, InaccessibleMessage):
            await call.message.edit_reply_markup(reply_markup=None)
            await call.message.answer("📌 Asosiy menyu:", reply_markup=main_kb())
        return

    balance = get_balance(call.from_user.id)
    cost    = amount * 1000

    if balance < cost:
        await call.answer("❌ Yetarli mablag' yo'q!", show_alert=True); return

    subtract_balance(call.from_user.id, cost)
    order_id = create_order(
        call.from_user.id, data['channel_id'], data['channel_link'],
        data['channel_title'], data['channel_members'], amount
    )

    post_text = (
        f"📢 <b>{data['channel_title']}</b>\n"
        f"🔗 {data['channel_link']}\n"
        f"👥 Obunachi: {data['channel_members']:,}\n\n"
        f"📊 <b>Buyurtma holati:</b>\n"
        f"🎯 Buyurtma miqdori: {amount}\n"
        f"✅ Tasdiqlangan: 0"
    )
    try:
        bot_info = await bot.get_me()
        post_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Obuna bo'ldim — 1 000 so'm ol!",
                url=f"https://t.me/{bot_info.username}?start=order_{order_id}"
            )
        ]])
        sent = await bot.send_message(
            str(EARNING_CHANNEL_ID), post_text,
            parse_mode="HTML", reply_markup=post_kb
        )
        update_order_message_id(order_id, sent.message_id)
    except Exception as e:
        logging.warning(f"Earning kanaliga yuborishda xato: {e}")

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        f"✅ <b>Buyurtma qabul qilindi!</b>\n\n"
        f"📢 Kanal: <b>{data['channel_title']}</b>\n"
        f"🎯 Buyurtma: <b>{amount} ta obunachi</b>\n"
        f"💰 To'landi: <b>{cost:,} so'm</b>\n\n"
        f"🆔 Buyurtma raqami: <b>#{order_id}</b>",
        reply_markup=main_kb(), parse_mode="HTML"
    )
    await state.clear()

@router.callback_query(F.data == "cancel_order")
async def cancel_order(call: CallbackQuery, state: FSMContext):
    assert call.message and not isinstance(call.message, InaccessibleMessage)
    await state.clear()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("❌ Buyurtma bekor qilindi.", reply_markup=main_kb())

# ══════════════════════════════════════════════
#              ADMIN PANEL
# ══════════════════════════════════════════════
@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    assert message.from_user
    if not is_admin(message.from_user.id):
        await message.answer("❌ Ruxsat yo'q!"); return
    await state.clear()
    pending = count_pending_payments()
    await message.answer(
        f"🔐 <b>ADMIN PANEL</b>\n\n"
        f"⏳ Kutayotgan to'lovlar: <b>{pending} ta</b>\n\n"
        f"Quyidan bo'limni tanlang:",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@router.message(F.text == "📊 Statistika")
async def admin_stats(message: Message):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    users  = count_users()
    orders = count_orders()
    pending = count_pending_payments()
    await message.answer(
        f"┌─────────────────────┐\n"
        f"│    📊 STATISTIKA        │\n"
        f"└─────────────────────┘\n\n"
        f"👥 Jami foydalanuvchilar: <b>{users:,}</b>\n"
        f"📦 Jami buyurtmalar:      <b>{orders:,}</b>\n"
        f"💳 Kutayotgan to'lovlar:  <b>{pending:,}</b>\n",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@router.message(F.text == "📋 Kutayotgan to'lovlar")
async def admin_pending(message: Message):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    conn = db(); c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE status='pending' ORDER BY id DESC LIMIT 20")
    rows = c.fetchall(); conn.close()

    if not rows:
        await message.answer("✅ Kutayotgan to'lovlar yo'q!", reply_markup=admin_kb()); return

    text = "📋 <b>Kutayotgan to'lovlar:</b>\n\n"
    for row in rows:
        pid, uid, uname, fname, amsg_id, status, amt, created_at = row
        uname_str = f"@{uname}" if uname else "—"
        text += f"• <b>#{pid}</b> | {fname} ({uname_str}) | ID: <code>{uid}</code>\n"

    await message.answer(text, reply_markup=admin_kb(), parse_mode="HTML")

@router.message(F.text == "✅ Aktiv buyurtmalar")
async def admin_orders(message: Message):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    orders = get_active_orders()
    if not orders:
        await message.answer("📭 Aktiv buyurtmalar yo'q!", reply_markup=admin_kb()); return

    text = "📦 <b>Aktiv buyurtmalar:</b>\n\n"
    for order in orders:
        oid = order[0]; ch_link = order[3]; ch_title = order[4]
        amount = order[6]; confirmed = order[7]
        text += (
            f"• <b>#{oid}</b> | {ch_title}\n"
            f"  🎯 {confirmed}/{amount} | 🔗 {ch_link}\n\n"
        )
    await message.answer(text, reply_markup=admin_kb(), parse_mode="HTML", disable_web_page_preview=True)

@router.message(F.text == "📣 Xabar yuborish")
async def admin_broadcast_start(message: Message, state: FSMContext):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    await message.answer(
        "📣 <b>Barcha foydalanuvchilarga xabar</b>\n\n"
        "Yubormoqchi bo'lgan xabarni yozing:\n"
        "(Bekor qilish uchun /admin)",
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_broadcast_msg)

@router.message(AdminState.waiting_broadcast_msg)
async def admin_broadcast_send(message: Message, state: FSMContext):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    await state.clear()

    conn = db(); c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall(); conn.close()

    sent = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, message.text or message.caption or "")
            sent += 1
        except Exception:
            pass

    await message.answer(
        f"✅ <b>Xabar yuborildi!</b>\n\n"
        f"📤 Yuborildi: <b>{sent}</b> ta\n"
        f"👥 Jami: <b>{len(users)}</b> ta",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@router.message(F.text == "🔙 Asosiy menyu")
async def admin_to_main(message: Message, state: FSMContext):
    assert message.from_user
    if not is_admin(message.from_user.id): return
    await state.clear()
    await message.answer("📌 Asosiy menyu:", reply_markup=main_kb())

# ══════════════════════════════════════════════
#              MAIN
# ══════════════════════════════════════════════
async def main():
    global bot
    load_config()
    init_db()
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=str(BOT_TOKEN))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
