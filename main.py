import asyncio
import logging
import sqlite3
import random
import string
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# =========================================================
# --- НАСТРОЙКИ ---
# =========================================================
TOKEN = "8544958075:AAG6dfQF1_lafEg1Y-yIAMoR_hr1e1pNPAs"
ADMIN_ID = 6203925408
BOT_USERNAME = "imperwon_bot"

DEFAULT_CARD_NUMBER = "2202208897924636"
DEFAULT_CARD_NAME = ""
DEFAULT_SUPPORT = "@imperfreaks"
DEFAULT_PRICE_SELL = 10.0
DEFAULT_PRICE_BUY = 8.0
DEFAULT_GAME_ID = "1000068"
DEFAULT_STOCK_THRESHOLD = 1_000_000
CURRENCY = "₽"
REF_BONUS_PERCENT = 0.02

# =========================================================
# --- БАЗА ДАННЫХ ---
# =========================================================
db = sqlite3.connect("imperator_shop.db")
cursor = db.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL,
    price REAL,
    status TEXT DEFAULT 'available'
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    game_id TEXT,
    amount REAL,
    total_price REAL,
    status TEXT,
    photo_id TEXT,
    promo_code TEXT,
    discount REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    referrer_id INTEGER DEFAULT NULL,
    coins_bonus REAL DEFAULT 0
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS blacklist (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS promos (
    code TEXT PRIMARY KEY,
    discount INTEGER,
    uses_left INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)""")
db.commit()

# =========================================================
# --- НАСТРОЙКИ ---
# =========================================================
def get_setting(key, default=None):
    cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def set_setting(key, value):
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()

_defaults = {
    "card_number": DEFAULT_CARD_NUMBER,
    "card_name": DEFAULT_CARD_NAME,
    "support": DEFAULT_SUPPORT,
    "price_sell": DEFAULT_PRICE_SELL,
    "price_buy": DEFAULT_PRICE_BUY,
    "game_id": DEFAULT_GAME_ID,
    "bank": 0,
    "paused_all": "0",
    "paused_buy": "0",
    "paused_sell": "0",
    "stock_threshold": DEFAULT_STOCK_THRESHOLD,
    "stock_alert_sent": "0",
}
for k, v in _defaults.items():
    if get_setting(k) is None:
        set_setting(k, v)

# =========================================================
# --- СОСТОЯНИЯ ---
# =========================================================
class BuyState(StatesGroup):
    waiting_for_game_id = State()
    waiting_for_amount = State()
    waiting_for_promo = State()
    waiting_for_receipt = State()

class SellState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_payout_method = State()
    waiting_for_payout_details = State()
    waiting_for_confirmation = State()

class BonusState(StatesGroup):
    waiting_for_method = State()
    waiting_for_game_id = State()
    waiting_for_card = State()
    waiting_for_amount = State()

class AdminState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_card_number = State()
    waiting_for_card_name = State()
    waiting_for_support = State()
    waiting_for_price_sell = State()
    waiting_for_price_buy = State()
    waiting_for_broadcast = State()
    waiting_for_user_search = State()
    waiting_for_game_id = State()
    waiting_for_bank = State()
    waiting_for_threshold = State()
    waiting_for_bl_reason = State()
    waiting_for_balance_coins = State()
    waiting_for_promo_code = State()
    waiting_for_promo_discount = State()
    waiting_for_promo_uses = State()

# =========================================================
# --- ИНИЦИАЛИЗАЦИЯ ---
# =========================================================
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
LAST_MESSAGES = {}

# =========================================================
# --- БД-ФУНКЦИИ ---
# =========================================================
def get_stock_total():
    cursor.execute("SELECT SUM(amount) FROM stock WHERE status='available'")
    res = cursor.fetchone()[0]
    return res if res else 0.0

def set_stock_total(value):
    """Устанавливает общее количество коинов на складе (одна запись)."""
    cursor.execute("DELETE FROM stock")
    if value > 0:
        cursor.execute("INSERT INTO stock (amount, price, status) VALUES (?, ?, 'available')",
                       (value, 0))
    db.commit()

def save_user(user_id, username, referrer_id=None):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, referrer_id) VALUES (?, ?, ?)",
                   (user_id, username, referrer_id))
    if username:
        cursor.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    db.commit()

def is_blacklisted(user_id):
    cursor.execute("SELECT 1 FROM blacklist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def get_bank():
    return float(get_setting("bank", 0) or 0)

def set_bank(value):
    set_setting("bank", float(value))

def is_paused_all():
    return get_setting("paused_all", "0") == "1"

def is_paused_buy():
    return get_setting("paused_buy", "0") == "1" or is_paused_all()

def is_paused_sell():
    return get_setting("paused_sell", "0") == "1" or is_paused_all()

def gen_promo_code(length=8):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

# =========================================================
# --- ХЕЛПЕРЫ ---
# =========================================================
async def clean_reply(message: types.Message, text: str, kb=None, state: FSMContext = None, parse_mode="HTML"):
    user_id = message.from_user.id
    try: await message.delete()
    except: pass

    last_id = LAST_MESSAGES.get(user_id)
    if last_id:
        try: await bot.delete_message(chat_id=message.chat.id, message_id=last_id)
        except: pass

    sent = await message.answer(text, reply_markup=kb, parse_mode=parse_mode)
    LAST_MESSAGES[user_id] = sent.message_id
    return sent

async def clean_callback(callback: types.CallbackQuery, text: str, kb=None, parse_mode="HTML"):
    user_id = callback.from_user.id
    try: await callback.message.delete()
    except: pass

    last_id = LAST_MESSAGES.get(user_id)
    if last_id:
        try: await bot.delete_message(chat_id=callback.message.chat.id, message_id=last_id)
        except: pass

    sent = await callback.message.answer(text, reply_markup=kb, parse_mode=parse_mode)
    LAST_MESSAGES[user_id] = sent.message_id
    return sent

# =========================================================
# --- КЛАВИАТУРЫ ---
# =========================================================
def main_menu_kb(user_id: int = 0):
    kb = [
        [InlineKeyboardButton(text="🩸 Купить коины", callback_data="menu_buy"),
         InlineKeyboardButton(text="💀 Продать коины", callback_data="menu_sell")],
        [InlineKeyboardButton(text="⚰️ Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="🦴 Рефералы", callback_data="menu_ref")],
        [InlineKeyboardButton(text="📜 Информация", callback_data="menu_info"),
         InlineKeyboardButton(text="🆘 Поддержка", callback_data="menu_support")],
    ]
    if user_id == ADMIN_ID:
        kb.append([InlineKeyboardButton(text="☠️ Админ-панель", callback_data="menu_admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_menu_kb():
    kb = [
        [InlineKeyboardButton(text="📦 Склад", callback_data="adm_stock"),
         InlineKeyboardButton(text="🏦 Банк", callback_data="adm_bank")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
         InlineKeyboardButton(text="📜 История", callback_data="adm_history")],
        [InlineKeyboardButton(text="🔍 Поиск жертвы", callback_data="adm_search"),
         InlineKeyboardButton(text="🚫 ЧС", callback_data="adm_bl_list")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast"),
         InlineKeyboardButton(text="💳 Реквизиты", callback_data="adm_card")],
        [InlineKeyboardButton(text="💱 Курсы", callback_data="adm_prices"),
         InlineKeyboardButton(text="🎮 Game ID", callback_data="adm_gameid")],
        [InlineKeyboardButton(text="👨‍💻 Поддержка", callback_data="adm_support"),
         InlineKeyboardButton(text="🎟 Промокоды", callback_data="adm_promos")],
        [InlineKeyboardButton(text="📉 Порог склада", callback_data="adm_threshold"),
         InlineKeyboardButton(text="⏸ Паузы", callback_data="adm_pause_menu")],
        [InlineKeyboardButton(text="⬅️ Вернуться в магазин", callback_data="menu_home")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главная", callback_data="menu_home")]
    ])

def cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_home")]
    ])

def admin_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])

# =========================================================
# --- ГЛАВНЫЙ ТЕКСТ ---
# =========================================================
def build_main_text():
    stock = get_stock_total()
    bank = get_bank()

    if stock > 10_000_000: si, sn = "🟢", "Полная бойня"
    elif stock > 1_000_000: si, sn = "🟡", "Есть чем поживиться"
    elif stock > 0: si, sn = "🟠", "Крови мало, спеши"
    else: si, sn = "🔴", "Склад опустошён"

    price_sell = float(get_setting("price_sell", DEFAULT_PRICE_SELL))
    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))

    return (
        f"<b>🩸 I M P E R A T O R   S H O P 🩸</b>\n"
        f"<i>Магазин, где коины льются рекой</i>\n\n"
        f"☠️ ─────  <b>СКЛАД</b>  ───── ☠️\n"
        f"  {si} Коинов: <b>{stock:,.0f} N</b>\n"
        f"  🏦 Банк: <b>{bank:.2f} {CURRENCY}</b>\n"
        f"  <i>{sn}</i>\n\n"
        f"🩸 ─────  <b>ТАРИФЫ</b>  ───── 🩸\n"
        f"  💀 <b>Забрать коины</b>  -  {price_sell} {CURRENCY} / 1кк\n"
        f"  🩸 <b>Слить коины</b>   -  {price_buy} {CURRENCY} / 1кк\n\n"
        f"<i>Выбери свою судьбу ↓</i>"
    )

# =========================================================
# --- СТАРТ ---
# =========================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    referrer_id = None
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].replace("ref_", ""))
            if referrer_id == message.from_user.id:
                referrer_id = None
        except: referrer_id = None

    save_user(message.from_user.id, message.from_user.username, referrer_id)

    if is_blacklisted(message.from_user.id):
        try: await message.delete()
        except: pass
        await message.answer("🚫 <b>Вы в чёрном списке.</b>\n\nОбратитесь в поддержку.", parse_mode="HTML")
        return

    user_id = message.from_user.id
    try: await message.delete()
    except: pass

    last_id = LAST_MESSAGES.get(user_id)
    if last_id:
        try: await bot.delete_message(chat_id=message.chat.id, message_id=last_id)
        except: pass

    sent = await message.answer(build_main_text(), reply_markup=main_menu_kb(user_id), parse_mode="HTML")
    LAST_MESSAGES[user_id] = sent.message_id

@dp.message(Command("home"))
async def cmd_home(message: types.Message, state: FSMContext):
    await state.clear()
    await cmd_start(message)

@dp.callback_query(F.data == "menu_home")
async def menu_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    save_user(callback.from_user.id, callback.from_user.username)
    await clean_callback(callback, build_main_text(), kb=main_menu_kb(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "menu_info")
async def menu_info(callback: types.CallbackQuery, state: FSMContext):
    text = (
        f"<b>📜  Информация об Imperator Shop</b>\n\n"
        f"☠️ ─────  <b>ПРАВИЛА</b>  ───── ☠️\n"
        f"  🩸 Только чистые сделки\n"
        f"  ⚡ Моментальные выплаты\n"
        f"  🔒 Работаем 24/7\n\n"
        f"🩸 ─────  <b>КУРСЫ</b>  ───── 🩸\n"
        f"  💀 Забрать коины: <b>{get_setting('price_sell')} {CURRENCY}</b> / 1кк\n"
        f"  🩸 Слить коины:   <b>{get_setting('price_buy')} {CURRENCY}</b> / 1кк\n\n"
        f"☠️ ─────  <b>КОНТАКТ</b>  ───── ☠️\n"
        f"  🆘 Поддержка: {get_setting('support')}\n\n"
        f"<i>Не пролей свою кровь зря 🩸</i>"
    )
    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "menu_support")
async def menu_support(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"🆘 <b>Поддержка Imperator Shop</b>\n\n"
        f"🩸 Все вопросы к: {get_setting('support')}",
        reply_markup=back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ПРОФИЛЬ ---
# =========================================================

@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    cursor.execute("SELECT username, coins_bonus FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    if not row:
        save_user(user_id, callback.from_user.username)
        row = (callback.from_user.username, 0)
    username, coins_bonus = row

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='completed'", (user_id,))
    buys, buys_sum = cursor.fetchone()
    buys = buys or 0; buys_sum = buys_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='sell_completed'", (user_id,))
    sells, sells_sum = cursor.fetchone()
    sells = sells or 0; sells_sum = abs(sells_sum or 0)

    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (user_id,))
    ref_count = cursor.fetchone()[0] or 0

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    text = (
        f"<b>⚰️  Ваш профиль</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🆔 ID: <code>{user_id}</code>\n"
        f"  📛 Имя: @{username or '-'}\n"
        f"  🦴 Бонусных коинов: <b>{coins_bonus:,.0f} N</b>\n"
        f"\n"
        f"🩸 ────  <b>ПОКУПКИ</b>  ──── 🩸\n"
        f"  💀 Заказов: <b>{buys}</b> на <b>{buys_sum:.2f} {CURRENCY}</b>\n"
        f"\n"
        f"☠️ ────  <b>ПРОДАЖИ</b>  ──── ☠️\n"
        f"  🩸 Заявок: <b>{sells}</b> на <b>{sells_sum:.2f} {CURRENCY}</b>\n"
        f"\n"
        f"🦴 ────  <b>РЕФЕРАЛЫ</b>  ──── 🦴\n"
        f"  👥 Приглашено: <b>{ref_count}</b>\n"
        f"  🔗 Ссылка:\n<code>{ref_link}</code>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Вывести бонусы", callback_data="bonus_withdraw")],
        [InlineKeyboardButton(text="🏠 Главная", callback_data="menu_home")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[user_id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ВЫВОД БОНУСОВ ---
# =========================================================

@dp.callback_query(F.data == "bonus_withdraw")
async def bonus_withdraw_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    cursor.execute("SELECT coins_bonus FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    bonus = row[0] if row else 0

    if bonus < 1_000_000:
        await callback.answer(
            "Минимум для вывода — 1 000 000 N бонусов.",
            show_alert=True
        )
        return

    text = (
        f"💰 <b>Вывод бонусных коинов</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🦴 У вас: <b>{bonus:,.0f} N</b>\n"
        f"  📉 Минимум: <b>1 000 000 N</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"<b>Как хотите получить вывод?</b>\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 На игровой ID", callback_data="bonus_method_game")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_home")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[user_id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "bonus_method_game")
async def bonus_method_game(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(bonus_method="game")
    await callback.message.edit_text(
        "🎮 <b>Шаг 1/2: Введите ваш игровой ID</b>\n\n"
        "<i>Куда зачислить коины.</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(BonusState.waiting_for_game_id)
    await callback.answer()

@dp.message(BonusState.waiting_for_game_id)
async def bonus_game_received(message: types.Message, state: FSMContext):
    await state.update_data(bonus_requisite=message.text.strip())
    user_id = message.from_user.id
    cursor.execute("SELECT coins_bonus FROM users WHERE user_id=?", (user_id,))
    bonus = cursor.fetchone()[0] or 0

    await clean_reply(
        message,
        f"💰 <b>Шаг 2/2: Сколько коинов вывести?</b>\n\n"
        f"  🦴 Доступно: <b>{bonus:,.0f} N</b>\n"
        f"  📉 Минимум: <b>1 000 000 N</b>",
        kb=cancel_kb(), state=state
    )
    await state.set_state(BonusState.waiting_for_amount)

@dp.message(BonusState.waiting_for_card)
async def bonus_card_received(message: types.Message, state: FSMContext):
    await state.update_data(bonus_requisite=message.text.strip())
    user_id = message.from_user.id
    cursor.execute("SELECT coins_bonus FROM users WHERE user_id=?", (user_id,))
    bonus = cursor.fetchone()[0] or 0

    # Курс обмена бонусов → деньги: используем price_buy
    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))
    max_money = (bonus / 1_000_000) * price_buy

    await clean_reply(
        message,
        f"💰 <b>Шаг 2/2: Сколько коинов вывести?</b>\n\n"
        f"  🦴 Доступно: <b>{bonus:,.0f} N</b>\n"
        f"  💵 Курс вывода: <b>{price_buy} {CURRENCY} / 1кк</b>\n"
        f"  💰 Максимум: <b>{max_money:.2f} {CURRENCY}</b>",
        kb=cancel_kb(), state=state
    )
    await state.set_state(BonusState.waiting_for_amount)

@dp.message(BonusState.waiting_for_amount)
async def bonus_amount_received(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(" ", "").replace(",", ""))
    except ValueError:
        await clean_reply(message, "❌ Введите корректное число.", kb=cancel_kb(), state=state)
        return

    if amount < 1_000_000:
        await clean_reply(message, "❌ Минимум — <b>1 000 000 N</b>.", kb=cancel_kb(), state=state)
        return

    user_id = message.from_user.id
    cursor.execute("SELECT coins_bonus FROM users WHERE user_id=?", (user_id,))
    bonus = cursor.fetchone()[0] or 0

    if amount > bonus:
        await clean_reply(message,
            f"❌ У вас только <b>{bonus:,.0f} N</b> бонусных коинов.",
            kb=cancel_kb(), state=state)
        return

    data = await state.get_data()
    method = data.get("bonus_method")
    requisite = data.get("bonus_requisite")
    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))
    money = (amount / 1_000_000) * price_buy

    # Создаём заявку
    cursor.execute(
        "INSERT INTO orders (user_id, game_id, amount, total_price, status, photo_id) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, method, -amount, -money, "bonus_waiting", requisite)
    )
    db.commit()
    order_id = cursor.lastrowid

    await clean_reply(
        message,
        f"⏳ <b>Заявка #{order_id} на вывод бонусов отправлена!</b>\n\n"
        f"Способ: <b>{'🎮 Коины на ID' if method == 'game' else '💳 Деньги'}</b>\n"
        f"Сумма: <b>{amount:,.0f} N</b>\n"
        f"ID: <code>{requisite}</code>\n\n"
        f"<i>Оператор обработает заявку в течение 5-15 минут.</i>",
        kb=back_kb(), state=state
    )
    await state.clear()

    # Уведомление админу
    method_text = "🎮 Коины на игровой ID" if method == "game" else "💳 Деньги на карту/СБП"
    admin_text = (
        f"🦴 <b>ЗАЯВКА НА ВЫВОД БОНУСОВ #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Юзер: @{message.from_user.username} (<code>{user_id}</code>)\n"
        f"📤 Способ: <b>{method_text}</b>\n"
        f"🦴 Списывается: <b>{amount:,.0f} N</b>\n"
        f"💰 ID: <code>{requisite}</code>\n"
        f"━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выдано", callback_data=f"bonus_ok_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bonus_rej_{order_id}")]
    ])
    await bot.send_message(ADMIN_ID, admin_text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("bonus_ok_"))
async def bonus_withdraw_ok(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[2]
    cursor.execute("SELECT user_id, amount, total_price FROM orders WHERE id=?", (order_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True); return

    user_id, amount, money = row

    # Списываем бонусы
    cursor.execute("UPDATE users SET coins_bonus = coins_bonus - ? WHERE user_id=?",
                   (abs(amount), user_id))
    cursor.execute("UPDATE orders SET status='bonus_completed' WHERE id=?", (order_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"🎉 <b>Заявка #{order_id} выполнена!</b>\n\n"
        f"🦴 Списано: <b>{abs(amount):,.0f} N</b>\n"
        f"💰 Получено: <b>{abs(money):.2f} {CURRENCY}</b>\n"
        f"☠️ Спасибо, что был с нами!",
        parse_mode="HTML"
    )
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>СТАТУС: ВЫДАНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Выдано!")

@dp.callback_query(F.data.startswith("bonus_rej_"))
async def bonus_withdraw_reject(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[2]
    cursor.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True); return
    user_id = row[0]
    cursor.execute("UPDATE orders SET status='bonus_rejected' WHERE id=?", (order_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"❌ <b>Заявка на вывод #{order_id} отклонена.</b>\n\n"
        f"Бонусы остались на вашем балансе. Свяжитесь с поддержкой.",
        parse_mode="HTML"
    )
    await callback.message.edit_text(
        callback.message.html_text + "\n\n❌ <b>СТАТУС: ОТКЛОНЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Отклонено.")
# =========================================================
# --- РЕФЕРАЛЫ ---
# =========================================================

@dp.callback_query(F.data == "menu_ref")
async def menu_ref(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (user_id,))
    ref_count = cursor.fetchone()[0] or 0

    cursor.execute("SELECT user_id, username, first_seen FROM users WHERE referrer_id=? ORDER BY first_seen DESC LIMIT 5", (user_id,))
    refs = cursor.fetchall()

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    bonus_percent = int(REF_BONUS_PERCENT * 100)

    text = (
        f"<b>🦴  Реферальная программа</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Приводи жертв — получай коины!\n\n"
        f"<b>Как это работает:</b>\n"
        f"  1. Отправь другу свою ссылку\n"
        f"  2. Он зайдёт в бота и купит коины\n"
        f"  3. Ты получишь <b>{bonus_percent}%</b> от его покупки в коинах\n\n"
        f"🩸 ───  <b>ВАША ССЫЛКА</b>  ─── 🩸\n"
        f"<code>{ref_link}</code>\n\n"
        f"👥 Приглашено: <b>{ref_count}</b>\n"
    )
    if refs:
        text += "\n☠️ ─────  <b>ПОСЛЕДНИЕ ЖЕРТВЫ</b>  ───── ☠️\n"
        for r_id, r_name, r_date in refs:
            text += f"  • @{r_name or '-'}\n"

    await callback.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")
    LAST_MESSAGES[user_id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ПРОМОКОД (команда) ---
# =========================================================

@dp.message(Command("promo"))
async def cmd_promo(message: types.Message, state: FSMContext):
    args = message.text.split()
    if len(args) < 2:
        await clean_reply(
            message,
            "🎟 <b>Использование:</b> <code>/promo КОД</code>",
            kb=back_kb(), state=state
        )
        return

    code = args[1].strip().upper()
    cursor.execute("SELECT code, discount, uses_left FROM promos WHERE code=?", (code,))
    row = cursor.fetchone()
    if not row:
        await clean_reply(message, f"❌ Промокод <code>{code}</code> не найден.", kb=back_kb(), state=state)
        return

    _, disc, left = row
    if left <= 0:
        await clean_reply(message, f"❌ Промокод <code>{code}</code> больше не работает.", kb=back_kb(), state=state)
        return

    await state.update_data(pending_promo=code)
    await clean_reply(
        message,
        f"✅ Промокод <code>{code}</code> сохранён.\n\n"
        f"Скидка: <b>{disc}%</b>\n"
        f"Осталось активаций: <b>{left}</b>\n\n"
        f"<i>Он применится при следующей покупке.</i>",
        kb=back_kb(), state=state
    )
    # =========================================================
# --- ПОКУПКА КОИНОВ ---
# =========================================================

@dp.callback_query(F.data == "menu_buy")
async def menu_buy(callback: types.CallbackQuery, state: FSMContext):
    if is_paused_buy():
        await callback.message.edit_text(
            "🌙 <b>Покупка временно приостановлена</b>\n\nЗагляните позже, бойня откроется утром.",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        await callback.answer()
        return

    stock = get_stock_total()
    if stock <= 0:
        await callback.message.edit_text(
            "⚠️ <b>Склад пуст!</b>\nОператор уже точит нож и пополняет запасы.",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🎮 <b>Шаг 1/4: Введите ваш игровой ID (Никнейм)</b>\n"
        "<i>Убедись, что ID указан верно — на неверный ID кровь не вернём.</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(BuyState.waiting_for_game_id)
    await callback.answer()

@dp.message(BuyState.waiting_for_game_id)
async def buy_amount(message: types.Message, state: FSMContext):
    await state.update_data(game_id=message.text)
    await clean_reply(
        message,
        "💎 <b>Шаг 2/4: Введите сумму покупки в N</b>\n"
        "<i>Пример: 1000000 (один миллион)</i>",
        kb=cancel_kb(), state=state
    )
    await state.set_state(BuyState.waiting_for_amount)

@dp.message(BuyState.waiting_for_amount)
async def buy_promo_ask(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(" ", "").replace(",", ""))
    except ValueError:
        await clean_reply(message, "❌ Введите корректное число.", kb=cancel_kb(), state=state)
        return

    stock = get_stock_total()
    if amount > stock:
        await clean_reply(message,
            f"❌ <b>Недостаточно товара!</b>\nМаксимум: <code>{stock:,.0f} N</code>",
            kb=cancel_kb(), state=state)
        return

    await state.update_data(amount=amount)

    data = await state.get_data()
    existing_promo = data.get("pending_promo")

    if existing_promo:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🎟 Применить {existing_promo}", callback_data="buy_promo_use")],
            [InlineKeyboardButton(text="⏭ Без промокода", callback_data="buy_promo_skip")]
        ])
        await clean_reply(
            message,
            f"🎟 У вас есть сохранённый промокод: <code>{existing_promo}</code>\n\n"
            f"Применить его к этому заказу?",
            kb=kb, state=state
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎟 Ввести промокод", callback_data="buy_promo_enter")],
            [InlineKeyboardButton(text="⏭ Без промокода", callback_data="buy_promo_skip")]
        ])
        await clean_reply(
            message,
            "🎟 <b>Шаг 3/4: Есть промокод?</b>\n"
            "<i>Если нет — просто пропусти.</i>",
            kb=kb, state=state
        )

@dp.callback_query(F.data == "buy_promo_enter")
async def buy_promo_enter(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎟 <b>Введите промокод</b>\n\n<i>Например: WELCOME10</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(BuyState.waiting_for_promo)
    await callback.answer()

@dp.message(BuyState.waiting_for_promo)
async def buy_promo_received(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    cursor.execute("SELECT code, discount, uses_left FROM promos WHERE code=?", (code,))
    row = cursor.fetchone()

    if not row:
        await clean_reply(message,
            f"❌ Промокод <code>{code}</code> не найден.\n\nПопробуй ещё раз или нажми «Отмена».",
            kb=cancel_kb(), state=state)
        return

    _, disc, left = row
    if left <= 0:
        await clean_reply(message,
            f"❌ Промокод <code>{code}</code> больше не работает.",
            kb=cancel_kb(), state=state)
        return

    await state.update_data(applied_promo=code, applied_discount=disc)
    data = await state.get_data()
    amount = data["amount"]
    price_sell = float(get_setting("price_sell", DEFAULT_PRICE_SELL))
    base_price = (amount / 1_000_000) * price_sell
    final_price = base_price * (1 - disc / 100)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 К оплате", callback_data="buy_confirm_invoice")]
    ])
    await clean_reply(
        message,
        f"✅ Промокод <code>{code}</code> применён!\n\n"
        f"  Скидка: <b>{disc}%</b>\n"
        f"  Было: <s>{base_price:.2f} {CURRENCY}</s>\n"
        f"  Стало: <b>{final_price:.2f} {CURRENCY}</b>",
        kb=kb, state=state
    )

@dp.callback_query(F.data == "buy_promo_use")
async def buy_promo_use(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    code = data.get("pending_promo")
    cursor.execute("SELECT discount, uses_left FROM promos WHERE code=?", (code,))
    row = cursor.fetchone()
    if not row or row[1] <= 0:
        await callback.answer("Промокод недействителен.", show_alert=True)
        await state.update_data(pending_promo=None)
        return

    disc, left = row
    await state.update_data(applied_promo=code, applied_discount=disc)
    amount = data["amount"]
    price_sell = float(get_setting("price_sell", DEFAULT_PRICE_SELL))
    base_price = (amount / 1_000_000) * price_sell
    final_price = base_price * (1 - disc / 100)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 К оплате", callback_data="buy_confirm_invoice")]
    ])
    await callback.message.edit_text(
        f"✅ Промокод <code>{code}</code> применён!\n\n"
        f"  Скидка: <b>{disc}%</b>\n"
        f"  Было: <s>{base_price:.2f} {CURRENCY}</s>\n"
        f"  Стало: <b>{final_price:.2f} {CURRENCY}</b>",
        reply_markup=kb, parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "buy_promo_skip")
async def buy_promo_skip(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    price_sell = float(get_setting("price_sell", DEFAULT_PRICE_SELL))
    final_price = (amount / 1_000_000) * price_sell

    await state.update_data(applied_promo=None, applied_discount=0)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 К оплате", callback_data="buy_confirm_invoice")]
    ])
    await callback.message.edit_text(
        f"💎 <b>Заказ без промокода</b>\n\n"
        f"  К оплате: <b>{final_price:.2f} {CURRENCY}</b>",
        reply_markup=kb, parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "buy_confirm_invoice")
async def buy_confirm_invoice(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    game_id = data["game_id"]
    applied_promo = data.get("applied_promo")
    applied_discount = data.get("applied_discount", 0)

    price_sell = float(get_setting("price_sell", DEFAULT_PRICE_SELL))
    base_price = (amount / 1_000_000) * price_sell
    final_price = base_price * (1 - applied_discount / 100)

    card_number = get_setting("card_number", DEFAULT_CARD_NUMBER)
    card_name = get_setting("card_name", DEFAULT_CARD_NAME)

    cursor.execute("""INSERT INTO orders
        (user_id, game_id, amount, total_price, status, promo_code, discount)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (callback.from_user.id, game_id, amount, final_price, "pending_payment",
         applied_promo, applied_discount))
    db.commit()
    order_id = cursor.lastrowid

    if applied_promo:
        cursor.execute("UPDATE promos SET uses_left = uses_left - 1 WHERE code=?", (applied_promo,))
        db.commit()

    text = (
        f"☠️ <b>Шаг 4/4: Оплата заказа #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🎮 Ваш ID: <code>{game_id}</code>\n"
        f"💎 Количество: <b>{amount:,.0f} N</b>\n"
    )
    if applied_promo:
        text += f"🎟 Промокод: <code>{applied_promo}</code> (-{applied_discount}%)\n"
        text += f"💵 К оплате: <s>{base_price:.2f}</s> <b>{final_price:.2f} {CURRENCY}</b>\n"
    else:
        text += f"💵 К оплате: <b>{final_price:.2f} {CURRENCY}</b>\n"
    text += (
        f"━━━━━━━━━━━━━━\n"
        f"💳 <b>Реквизиты для перевода:</b>\n"
        f"┗ Карта: <code>{card_number}</code>\n"
        f"🩸 <b>После оплаты отправь скриншот чека прямо в этот чат.</b>"
    )

    await callback.message.edit_text(text, reply_markup=cancel_kb(), parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.update_data(order_id=order_id)
    await state.set_state(BuyState.waiting_for_receipt)
    await callback.answer()

@dp.message(BuyState.waiting_for_receipt, F.photo)
async def handle_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")

    if not order_id:
        cursor.execute("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
                       (message.from_user.id,))
        row = cursor.fetchone()
        if not row:
            await clean_reply(message, "❌ Заказ не найден.", kb=back_kb(), state=state)
            await state.clear()
            return
        order_id = row[0]

    cursor.execute("SELECT amount, total_price, game_id FROM orders WHERE id=?", (order_id,))
    order = cursor.fetchone()
    if not order:
        await clean_reply(message, "❌ Заказ не найден.", kb=back_kb(), state=state)
        await state.clear()
        return

    amount, price, game_id = order
    photo_id = message.photo[-1].file_id

    cursor.execute("UPDATE orders SET photo_id=?, status='checking' WHERE id=?", (photo_id, order_id))
    db.commit()

    sent = await message.answer(
        "🩸 <b>Чек получен и отправлен на проверку!</b>\n\n"
        "☠️ Оператор проверит поступление и выдаст коины в течение 5-15 минут.",
        reply_markup=back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[message.from_user.id] = sent.message_id

    admin_text = (
        f"🩸 <b>НОВАЯ ЗАЯВКА НА ПОКУПКУ #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Жертва: @{message.from_user.username} (<code>{message.from_user.id}</code>)\n"
        f"🎮 Игровой ID: <code>{game_id}</code>\n"
        f"💎 Сумма: <b>{amount:,.0f} N</b>\n"
        f"💰 К оплате: <b>{price:.2f} {CURRENCY}</b>\n"
        f"━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{order_id}")]
    ])
    await bot.send_photo(ADMIN_ID, photo_id, caption=admin_text, reply_markup=kb, parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_order(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[1]
    cursor.execute("SELECT user_id, amount, game_id, total_price FROM orders WHERE id=?", (order_id,))
    order = cursor.fetchone()

    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    user_id, amount, game_id, total_price = order
    cursor.execute("UPDATE orders SET status='completed' WHERE id=?", (order_id,))

    # Списываем со склада общую сумму
    new_stock = get_stock_total() - amount
    if new_stock < 0: new_stock = 0
    set_stock_total(new_stock)

    # Реферальный бонус
    cursor.execute("SELECT referrer_id FROM users WHERE user_id=?", (user_id,))
    ref_row = cursor.fetchone()
    if ref_row and ref_row[0]:
        referrer_id = ref_row[0]
        bonus_coins = amount * REF_BONUS_PERCENT
        cursor.execute("UPDATE users SET coins_bonus = coins_bonus + ? WHERE user_id=?",
                       (bonus_coins, referrer_id))
        try:
            await bot.send_message(
                referrer_id,
                f"🦴 <b>Бонус за жертву!</b>\n\n"
                f"Один из твоих рефералов купил <b>{amount:,.0f} N</b>.\n"
                f"🩸 Тебе начислено: <b>{bonus_coins:,.0f} N</b> бонусных коинов.",
                parse_mode="HTML"
            )
        except: pass

    db.commit()

    await bot.send_message(
        user_id,
        f"🎉 <b>Заказ #{order_id} выполнен!</b>\n\n"
        f"💎 <code>{amount:,.0f} N</code> зачислены на игровой ID: <code>{game_id}</code>\n"
        f"☠️ Спасибо за сделку!",
        parse_mode="HTML"
    )

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n✅ <b>СТАТУС: ВЫПОЛНЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Заказ выдан!")

    await check_stock_threshold_alert()

@dp.callback_query(F.data.startswith("reject_"))
async def reject_order(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[1]
    cursor.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cursor.fetchone()
    if not row:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    user_id = row[0]
    cursor.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"❌ <b>Заказ #{order_id} отклонён.</b>\n\n"
        f"Кровь не поступила или чек поддельный. Напиши в поддержку.",
        parse_mode="HTML"
    )
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n❌ <b>СТАТУС: ОТКЛОНЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Заказ отклонён.")

# =========================================================
# --- ПРОДАЖА КОИНОВ ---
# =========================================================

@dp.callback_query(F.data == "menu_sell")
async def menu_sell(callback: types.CallbackQuery, state: FSMContext):
    if is_paused_sell():
        await callback.message.edit_text(
            "🌙 <b>Скупка временно приостановлена</b>\n\nЗагляните позже.",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        await callback.answer()
        return

    bank = get_bank()
    if bank <= 0:
        await callback.message.edit_text(
            "⚠️ <b>Банк пуст!</b>\n\nМы временно не можем выкупать коины.",
            reply_markup=back_kb(), parse_mode="HTML"
        )
        await callback.answer()
        return

    price_buy = get_setting("price_buy", DEFAULT_PRICE_BUY)
    shop_game_id = get_setting("game_id", DEFAULT_GAME_ID)

    await callback.message.edit_text(
        f"💀 <b>Слив коинов в Imperator Shop</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  💱 Курс скупки: <b>{price_buy} {CURRENCY}</b> / 1кк\n"
        f"  🏦 Банк магазина: <b>{bank:.2f} {CURRENCY}</b>\n"
        f"  🎮 Наш игровой ID: <code>{shop_game_id}</code>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"<b>Шаг 1/3: Введите количество N для слива</b>\n\n"
        f"<i>Минимум: 1 000 000 N</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(SellState.waiting_for_amount)
    await callback.answer()

@dp.message(SellState.waiting_for_amount)
async def sell_show_instructions(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(" ", "").replace(",", ""))
    except ValueError:
        await clean_reply(message, "❌ Введите корректное число.", kb=cancel_kb(), state=state)
        return

    if amount < 1_000_000:
        await clean_reply(message,
            "❌ Минимальная сумма слива — <b>1 000 000 N</b>.",
            kb=cancel_kb(), state=state)
        return

    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))
    total_price = (amount / 1_000_000) * price_buy
    bank = get_bank()

    if total_price > bank:
        max_amount = (bank / price_buy) * 1_000_000
        await clean_reply(message,
            f"❌ <b>Банк не может столько выкупить!</b>\n\n"
            f"  🏦 Доступно: <b>{bank:.2f} {CURRENCY}</b>\n"
            f"  💎 Максимум: <b>{max_amount:,.0f} N</b>",
            kb=cancel_kb(), state=state)
        return

    shop_game_id = get_setting("game_id", DEFAULT_GAME_ID)
    await state.update_data(amount=amount, total_price=total_price)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 На карту", callback_data="payout_card")],
        [InlineKeyboardButton(text="📱 На телефон (СБП)", callback_data="payout_phone")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_home")]
    ])
    await clean_reply(
        message,
        f"📋 <b>Шаг 2/3: Переведи коины нам</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🎮 Игровой ID магазина: <code>{shop_game_id}</code>\n"
        f"  💎 Сумма перевода: <b>{amount:,.0f} N</b>\n"
        f"  💰 Ты получишь: <b>{total_price:.2f} {CURRENCY}</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"⚠️ Зайди в игру, переведи коины на наш ID.\n\n"
        f"<b>Шаг 3/3: Куда перевести тебе деньги?</b>",
        kb=kb, state=state
    )
    await state.set_state(SellState.waiting_for_payout_method)

@dp.callback_query(F.data == "payout_card", SellState.waiting_for_payout_method)
async def payout_card_choice(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💳 <b>Введите номер твоей карты</b>\n\n<i>Например: 2200 0000 0000 0000</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(SellState.waiting_for_payout_details)
    await callback.answer()

@dp.callback_query(F.data == "payout_phone", SellState.waiting_for_payout_method)
async def payout_phone_choice(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📱 <b>Введите номер телефона (СБП)</b>\n\n<i>Например: +7 999 123-45-67</i>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(SellState.waiting_for_payout_details)
    await callback.answer()

@dp.message(SellState.waiting_for_payout_details)
async def payout_details_received(message: types.Message, state: FSMContext):
    payout_details = message.text.strip()
    data = await state.get_data()
    amount = data["amount"]
    total_price = data["total_price"]
    shop_game_id = get_setting("game_id", DEFAULT_GAME_ID)
    await state.update_data(payout_details=payout_details)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я перевёл коины", callback_data="sell_i_paid")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu_home")]
    ])
    await clean_reply(
        message,
        f"📋 <b>Проверь данные:</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🎮 Куда: <code>{shop_game_id}</code>\n"
        f"  💎 Сумма: <b>{amount:,.0f} N</b>\n"
        f"  💰 К получению: <b>{total_price:.2f} {CURRENCY}</b>\n"
        f"  💳 Реквизит: <code>{payout_details}</code>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"Переведи коины и нажми кнопку ниже.",
        kb=kb, state=state
    )
    await state.set_state(SellState.waiting_for_confirmation)

@dp.callback_query(F.data == "sell_i_paid", SellState.waiting_for_confirmation)
async def sell_user_confirmed(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    total_price = data["total_price"]
    payout_details = data["payout_details"]

    cursor.execute(
        "INSERT INTO orders (user_id, game_id, amount, total_price, status, photo_id) VALUES (?, ?, ?, ?, ?, ?)",
        (callback.from_user.id, "-", -amount, -total_price, "sell_waiting_admin", payout_details)
    )
    db.commit()
    order_id = cursor.lastrowid

    await callback.message.edit_text(
        f"⏳ <b>Заявка #{order_id} отправлена оператору!</b>\n\n"
        f"🩸 Мы проверим поступление коинов и переведём деньги в течение 5 минут.",
        reply_markup=back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.clear()

    admin_text = (
        f"💸 <b>НОВАЯ ЗАЯВКА НА СЛИВ #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 Жертва: @{callback.from_user.username} (<code>{callback.from_user.id}</code>)\n"
        f"💎 Сливает: <b>{amount:,.0f} N</b>\n"
        f"💰 К выплате: <b>{total_price:.2f} {CURRENCY}</b>\n"
        f"💳 Реквизит: <code>{payout_details}</code>\n"
        f"━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Коины получены", callback_data=f"sell_confirm_{order_id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"sell_reject_{order_id}")]
    ])
    await bot.send_message(ADMIN_ID, admin_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("sell_confirm_"))
async def sell_confirm(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[2]
    cursor.execute("SELECT user_id, amount, total_price, photo_id FROM orders WHERE id=?", (order_id,))
    order = cursor.fetchone()

    if not order:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    user_id, amount, total_price, payout_details = order
    cursor.execute("UPDATE orders SET status='sell_awaiting_payout' WHERE id=?", (order_id,))

    # Возвращаем коины на склад
    new_stock = get_stock_total() + abs(amount)
    set_stock_total(new_stock)

    # Списываем с банка
    new_bank = get_bank() - abs(total_price)
    if new_bank < 0: new_bank = 0
    set_bank(new_bank)
    db.commit()

    await bot.send_message(
        user_id,
        f"🩸 <b>Коины получены! Заявка #{order_id}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 К выплате: <b>{abs(total_price):.2f} {CURRENCY}</b>\n"
        f"💳 Реквизит: <code>{payout_details}</code>\n"
        f"━━━━━━━━━━━━━━\n"
        f"<i>Оператор переведёт деньги в течение 5-15 минут.</i>",
        parse_mode="HTML"
    )

    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Деньги отправлены", callback_data=f"sell_paid_{order_id}")]
    ])
    await callback.message.edit_text(
        callback.message.html_text + "\n\n✅ <b>Коины подтверждены. Ждём выплату.</b>",
        reply_markup=new_kb, parse_mode="HTML"
    )
    await callback.answer("Коины подтверждены!")

@dp.callback_query(F.data.startswith("sell_paid_"))
async def sell_paid(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[2]
    cursor.execute("SELECT user_id, total_price FROM orders WHERE id=?", (order_id,))
    row = cursor.fetchone()

    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    user_id, total_price = row
    total_price = abs(total_price)
    cursor.execute("UPDATE orders SET status='sell_completed' WHERE id=?", (order_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"🎉 <b>Заявка #{order_id} завершена!</b>\n\n"
        f"💸 <code>{total_price:.2f} {CURRENCY}</code> отправлены на твои реквизиты.\n"
        f"☠️ Спасибо за сделку!",
        parse_mode="HTML"
    )
    await callback.message.edit_text(
        callback.message.html_text + "\n\n💸 <b>СТАТУС: ВЫПЛАЧЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Заявка закрыта!")

@dp.callback_query(F.data.startswith("sell_reject_"))
async def sell_reject(callback: types.CallbackQuery):
    order_id = callback.data.split("_")[2]
    cursor.execute("SELECT user_id FROM orders WHERE id=?", (order_id,))
    row = cursor.fetchone()

    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    user_id = row[0]
    cursor.execute("UPDATE orders SET status='sell_rejected' WHERE id=?", (order_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"❌ <b>Заявка на слив #{order_id} отклонена.</b>\n\n"
        f"Коины не поступили. Свяжись с поддержкой.",
        parse_mode="HTML"
    )
    await callback.message.edit_text(
        callback.message.html_text + "\n\n❌ <b>СТАТУС: ОТКЛОНЕНО</b>",
        parse_mode="HTML"
    )
    await callback.answer("Заявка отклонена.")

# =========================================================
# --- ПРОВЕРКА ПОРОГА СКЛАДА ---
# =========================================================
async def check_stock_threshold_alert():
    stock = get_stock_total()
    threshold = float(get_setting("stock_threshold", DEFAULT_STOCK_THRESHOLD))
    alert_sent = get_setting("stock_alert_sent", "0") == "1"

    if stock < threshold and not alert_sent:
        set_setting("stock_alert_sent", "1")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🩸 <b>ВНИМАНИЕ: СКЛАД ПАДАЕТ!</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"  📦 Остаток: <b>{stock:,.0f} N</b>\n"
                f"  📉 Порог: <b>{threshold:,.0f} N</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"<i>Срочно пополни склад!</i>",
                parse_mode="HTML"
            )
        except: pass
    elif stock >= threshold and alert_sent:
        set_setting("stock_alert_sent", "0")
        # =========================================================
# --- АДМИН ПАНЕЛЬ ---
# =========================================================

@dp.message(Command("admin"))
async def admin_cmd(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    user_id = message.from_user.id
    try: await message.delete()
    except: pass

    last_id = LAST_MESSAGES.get(user_id)
    if last_id:
        try: await bot.delete_message(chat_id=message.chat.id, message_id=last_id)
        except: pass

    sent = await message.answer(
        "☠️ <b>Панель управления Imperator Shop</b>\n\nВыбери действие:",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[user_id] = sent.message_id

@dp.callback_query(F.data == "menu_admin")
async def menu_admin(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True)
        return
    await callback.message.edit_text(
        "☠️ <b>Панель управления Imperator Shop</b>\n\nВыбери действие:",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_back")
async def adm_back(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "☠️ <b>Панель управления Imperator Shop</b>\n\nВыбери действие:",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- СКЛАД (как банк) ---
# =========================================================

@dp.callback_query(F.data == "adm_stock")
async def adm_stock(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    stock = get_stock_total()
    bank = get_bank()
    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))
    max_n = (bank / price_buy) * 1_000_000 if price_buy else 0

    text = (
        f"📦 <b>Имперский склад</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  📦 Коинов: <b>{stock:,.0f} N</b>\n"
        f"  🏦 Банк: <b>{bank:.2f} {CURRENCY}</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"<i>Из склада списываются коины при продаже игрокам.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Установить количество", callback_data="adm_stock_set")],
        [InlineKeyboardButton(text="➕ Пополнить", callback_data="adm_stock_add")],
        [InlineKeyboardButton(text="🗑 Обнулить", callback_data="adm_stock_zero")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_stock_set")
async def adm_stock_set(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "✏️ <b>Установить склад</b>\n\nВведи количество N:",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.update_data(stock_action="set")
    await state.set_state(AdminState.waiting_for_amount)
    await callback.answer()

@dp.callback_query(F.data == "adm_stock_add")
async def adm_stock_add(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "➕ <b>Пополнить склад</b>\n\nНа сколько N пополнить?",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.update_data(stock_action="add")
    await state.set_state(AdminState.waiting_for_amount)
    await callback.answer()

@dp.callback_query(F.data == "adm_stock_zero")
async def adm_stock_zero(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return

    stock = get_stock_total()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, обнулить", callback_data="adm_stock_zero_ok")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm_stock")]
    ])
    await callback.message.edit_text(
        f"🗑 <b>Обнулить склад?</b>\n\n"
        f"  📦 Сейчас: <code>{stock:,.0f} N</code>\n\n"
        f"⚠️ Действие необратимо.",
        reply_markup=kb, parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_stock_zero_ok")
async def adm_stock_zero_ok(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    set_stock_total(0)
    await callback.answer("Склад обнулён.", show_alert=True)
    await adm_stock(callback, state)

@dp.message(AdminState.waiting_for_amount)
async def adm_stock_amount_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text.replace(" ", "").replace(",", "."))
        data = await state.get_data()
        action = data.get("stock_action", "set")

        if action == "add":
            new_total = get_stock_total() + val
        else:
            new_total = val

        if new_total < 0:
            new_total = 0

        set_stock_total(new_total)

        sent = await message.answer(
            f"✅ <b>Склад обновлён</b>\n\n"
            f"  📦 Новое количество: <code>{new_total:,.0f} N</code>",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()

        threshold = float(get_setting("stock_threshold", DEFAULT_STOCK_THRESHOLD))
        if new_total >= threshold:
            set_setting("stock_alert_sent", "0")
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- БАНК ---
# =========================================================

@dp.callback_query(F.data == "adm_bank")
async def adm_bank(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    bank = get_bank()
    price_buy = float(get_setting("price_buy", DEFAULT_PRICE_BUY))
    max_n = (bank / price_buy) * 1_000_000 if price_buy else 0

    text = (
        f"🏦 <b>Банк магазина</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  💰 Баланс: <b>{bank:.2f} {CURRENCY}</b>\n"
        f"  💎 Хватит на выкуп: <b>{max_n:,.0f} N</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"<i>Из банка списываются выплаты игрокам при скупке.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Установить сумму", callback_data="adm_bank_set")],
        [InlineKeyboardButton(text="➕ Пополнить", callback_data="adm_bank_add")],
        [InlineKeyboardButton(text="🗑 Обнулить", callback_data="adm_bank_zero")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_bank_set")
async def adm_bank_set(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "✏️ <b>Установить банк</b>\n\nВведи сумму в рублях:",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.update_data(bank_action="set")
    await state.set_state(AdminState.waiting_for_bank)
    await callback.answer()

@dp.callback_query(F.data == "adm_bank_add")
async def adm_bank_add(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "➕ <b>Пополнить банк</b>\n\nНа сколько рублей пополнить?",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.update_data(bank_action="add")
    await state.set_state(AdminState.waiting_for_bank)
    await callback.answer()

@dp.callback_query(F.data == "adm_bank_zero")
async def adm_bank_zero(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    set_bank(0)
    await callback.answer("Банк обнулён.", show_alert=True)
    await adm_bank(callback, state)

@dp.message(AdminState.waiting_for_bank)
async def adm_bank_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text.replace(" ", "").replace(",", "."))
        data = await state.get_data()
        action = data.get("bank_action", "set")

        if action == "add":
            new_val = get_bank() + val
        else:
            new_val = val

        set_bank(new_val)
        sent = await message.answer(
            f"✅ <b>Банк обновлён</b>\n\n  💰 Новый баланс: <code>{new_val:.2f} {CURRENCY}</code>",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- СТАТИСТИКА ---
# =========================================================

@dp.callback_query(F.data == "adm_stats")
async def adm_stats(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE status='completed'")
    c_count, c_sum = cursor.fetchone(); c_count = c_count or 0; c_sum = c_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE status='sell_completed'")
    s_count, s_sum = cursor.fetchone(); s_count = s_count or 0; s_sum = abs(s_sum or 0)

    cursor.execute("SELECT COUNT(*) FROM orders WHERE status IN ('checking','sell_waiting_admin','sell_awaiting_payout')")
    pend = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM orders WHERE status IN ('rejected','sell_rejected')")
    rej = cursor.fetchone()[0] or 0

    cursor.execute("SELECT SUM(amount) FROM stock WHERE status='available'")
    left = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM users")
    users = cursor.fetchone()[0] or 0

    profit = c_sum - s_sum
    p_emoji = "📈" if profit >= 0 else "📉"

    text = (
        f"📊 <b>Статистика Imperator Shop</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"📦 Склад: <b>{left:,.0f} N</b>\n"
        f"🏦 Банк: <b>{get_bank():.2f} {CURRENCY}</b>\n"
        f"\n"
        f"☠️ ─────  <b>ЗАКАЗЫ</b>  ───── ☠️\n"
        f"  ✅ Продано: <b>{c_count}</b>\n"
        f"  🩸 Куплено: <b>{s_count}</b>\n"
        f"  ⏳ На проверке: <b>{pend}</b>\n"
        f"  ❌ Отклонено: <b>{rej}</b>\n"
        f"\n"
        f"🩸 ─────  <b>ФИНАНСЫ</b>  ───── 🩸\n"
        f"  💰 Доход: <b>+{c_sum:.2f} {CURRENCY}</b>\n"
        f"  💸 Расход: <b>-{s_sum:.2f} {CURRENCY}</b>\n"
        f"  ─────\n"
        f"  {p_emoji} Чистая прибыль: <b>{profit:+.2f} {CURRENCY}</b>"
    )
    await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ИСТОРИЯ ---
# =========================================================

@dp.callback_query(F.data == "adm_history")
async def adm_history(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    cursor.execute("SELECT id, user_id, amount, total_price, status FROM orders ORDER BY id DESC LIMIT 10")
    orders = cursor.fetchall()
    if not orders:
        await callback.answer("Заказов пока нет.", show_alert=True); return

    emo = {"pending_payment":"🕐","checking":"⏳","completed":"✅","rejected":"❌",
           "sell_waiting_admin":"💸","sell_awaiting_payout":"💳","sell_completed":"🎉","sell_rejected":"🚫",
           "bonus_waiting":"🦴","bonus_completed":"🎁","bonus_rejected":"🚫"}
    text = "📜 <b>Последние 10 заказов:</b>\n━━━━━━━━━━━━━━\n"
    for o_id, u_id, amt, pr, st in orders:
        text += f"{emo.get(st,'❔')} <b>#{o_id}</b> · <code>{amt:,.0f}N</code> · <b>{abs(pr):.2f}₽</b> · <code>{u_id}</code>\n"

    await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ПОИСК ЮЗЕРА ---
# =========================================================

@dp.callback_query(F.data == "adm_search")
async def adm_search(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    await callback.message.edit_text(
        "🔍 <b>Поиск жертвы</b>\n\n"
        "• Telegram ID: <code>6203925408</code>\n"
        "• @username: <code>@haslich</code>",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_user_search)
    await callback.answer()

@dp.message(AdminState.waiting_for_user_search)
async def adm_search_result(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return

    query = message.text.strip()
    user_id = None; username_found = None

    if query.startswith("@"):
        cursor.execute("SELECT user_id, username FROM users WHERE LOWER(username)=?", (query[1:].lower(),))
        row = cursor.fetchone()
        if row: user_id, username_found = row
    else:
        try:
            user_id = int(query)
            cursor.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
            username_found = row[0] if row else None
        except ValueError:
            cursor.execute("SELECT user_id, username FROM users WHERE LOWER(username)=?", (query.lower(),))
            row = cursor.fetchone()
            if row: user_id, username_found = row

    if not user_id:
        await clean_reply(message, f"❌ Пользователь <code>{query}</code> не найден.",
                          kb=admin_back_kb(), state=state)
        await state.clear()
        return

    await show_user_profile(message, state, user_id, username_found)

async def show_user_profile(message: types.Message, state: FSMContext, user_id: int, username_found=None):
    cursor.execute("SELECT username, coins_bonus, referrer_id FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    coins_bonus = row[1] if row else 0
    referrer_id = row[2] if row else None
    username = username_found or (row[0] if row else None)

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='completed'", (user_id,))
    buys, buys_sum = cursor.fetchone(); buys = buys or 0; buys_sum = buys_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='sell_completed'", (user_id,))
    sells, sells_sum = cursor.fetchone(); sells = sells or 0; sells_sum = abs(sells_sum or 0)

    bl = is_blacklisted(user_id)

    text = (
        f"👤 <b>Профиль жертвы</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🆔 ID: <code>{user_id}</code>\n"
        f"  📛 Юзер: @{username or '-'}\n"
        f"  🦴 Бонусных коинов: <b>{coins_bonus:,.0f} N</b>\n"
        f"  🚫 ЧС: <b>{'ДА' if bl else 'нет'}</b>\n"
        f"  👥 Пригласил: <code>{referrer_id or '-'}</code>\n"
        f"\n"
        f"🩸 Покупок: <b>{buys}</b> на <b>{buys_sum:.2f} {CURRENCY}</b>\n"
        f"💀 Продаж: <b>{sells}</b> на <b>{sells_sum:.2f} {CURRENCY}</b>\n"
    )

    kb_rows = [
        [InlineKeyboardButton(text="💰 Начислить коины", callback_data=f"adm_bal_{user_id}"),
         InlineKeyboardButton(text="💸 Списать коины", callback_data=f"adm_balneg_{user_id}")],
    ]
    if bl:
        kb_rows.append([InlineKeyboardButton(text="✅ Убрать из ЧС", callback_data=f"adm_unbl_{user_id}")])
    else:
        kb_rows.append([InlineKeyboardButton(text="🚫 Добавить в ЧС", callback_data=f"adm_bl_{user_id}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    sent = await message.answer(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    await state.clear()

# =========================================================
# --- НАЧИСЛЕНИЕ / СПИСАНИЕ КОИНОВ ---
# =========================================================

@dp.callback_query(F.data.startswith("adm_balneg_"))
async def adm_balance_sub(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    target_id = int(callback.data.split("_")[2])
    await state.update_data(target_user=target_id, balance_sign=-1)
    await callback.message.edit_text(
        f"💸 <b>Списание коинов</b>\n\n"
        f"Юзер: <code>{target_id}</code>\n\n"
        f"Сколько коинов списать?",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_balance_coins)
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_bal_"))
async def adm_balance_add(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    target_id = int(callback.data.split("_")[2])
    await state.update_data(target_user=target_id, balance_sign=1)
    await callback.message.edit_text(
        f"💰 <b>Начисление коинов</b>\n\n"
        f"Юзер: <code>{target_id}</code>\n\n"
        f"Сколько коинов начислить?",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_balance_coins)
    await callback.answer()

@dp.message(AdminState.waiting_for_balance_coins)
async def adm_balance_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text.replace(" ", ""))
        data = await state.get_data()
        target = data["target_user"]
        sign = data.get("balance_sign", 1)

        cursor.execute("UPDATE users SET coins_bonus = coins_bonus + ? WHERE user_id=?",
                       (val * sign, target))
        db.commit()

        cursor.execute("SELECT coins_bonus FROM users WHERE user_id=?", (target,))
        new_bal = cursor.fetchone()[0]

        sent = await message.answer(
            f"✅ <b>Баланс обновлён</b>\n\n"
            f"  👤 Юзер: <code>{target}</code>\n"
            f"  🦴 Новый бонус: <b>{new_bal:,.0f} N</b>",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()

        try:
            sign_text = "начислено" if sign > 0 else "списано"
            await bot.send_message(
                target,
                f"🦴 <b>Изменение бонусного баланса</b>\n\n"
                f"Тебе {sign_text}: <b>{abs(val):,.0f} N</b>",
                parse_mode="HTML"
            )
        except: pass
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- ЧЁРНЫЙ СПИСОК ---
# =========================================================

@dp.callback_query(F.data == "adm_bl_list")
async def adm_bl_list(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    cursor.execute("SELECT user_id, reason, added_at FROM blacklist ORDER BY added_at DESC LIMIT 20")
    rows = cursor.fetchall()

    text = "🚫 <b>Чёрный список</b>\n━━━━━━━━━━━━━━\n"
    if not rows:
        text += "\n<i>Пусто. Все живы.</i>"
    else:
        for uid, reason, dt in rows:
            text += f"  🩸 <code>{uid}</code> — {reason or '—'}\n"

    await callback.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data.startswith("adm_unbl_"))
async def adm_unbl(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    target_id = int(callback.data.split("_")[2])
    cursor.execute("DELETE FROM blacklist WHERE user_id=?", (target_id,))
    db.commit()
    await callback.answer("Убран из ЧС.", show_alert=True)
    await show_user_profile_from_cb(callback, state, target_id)

@dp.callback_query(F.data.startswith("adm_bl_"))
async def adm_bl_ask(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    if callback.data == "adm_bl_list":
        return
    parts = callback.data.split("_")
    if len(parts) < 3:
        await callback.answer(); return
    try:
        target_id = int(parts[2])
    except ValueError:
        await callback.answer(); return

    await state.update_data(bl_target=target_id)
    await callback.message.edit_text(
        f"🚫 <b>Добавить в ЧС</b>\n\nЮзер: <code>{target_id}</code>\n\nПричина (или напиши «-»):",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_bl_reason)
    await callback.answer()

@dp.message(AdminState.waiting_for_bl_reason)
async def adm_bl_reason(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    reason = message.text.strip()
    if reason == "-": reason = ""
    data = await state.get_data()
    target = data["bl_target"]

    cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason) VALUES (?, ?)", (target, reason))
    db.commit()

    sent = await message.answer(
        f"✅ Юзер <code>{target}</code> добавлен в ЧС.\nПричина: {reason or '—'}",
        reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    try: await message.delete()
    except: pass
    await state.clear()

async def show_user_profile_from_cb(callback: types.CallbackQuery, state: FSMContext, user_id: int):
    cursor.execute("SELECT username, coins_bonus, referrer_id FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    coins_bonus = row[1] if row else 0
    referrer_id = row[2] if row else None
    username = row[0] if row else None

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='completed'", (user_id,))
    buys, buys_sum = cursor.fetchone(); buys = buys or 0; buys_sum = buys_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(total_price) FROM orders WHERE user_id=? AND status='sell_completed'", (user_id,))
    sells, sells_sum = cursor.fetchone(); sells = sells or 0; sells_sum = abs(sells_sum or 0)

    bl = is_blacklisted(user_id)

    text = (
        f"👤 <b>Профиль жертвы</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🆔 ID: <code>{user_id}</code>\n"
        f"  📛 Юзер: @{username or '-'}\n"
        f"  🦴 Бонусных коинов: <b>{coins_bonus:,.0f} N</b>\n"
        f"  🚫 ЧС: <b>{'ДА' if bl else 'нет'}</b>\n"
        f"  👥 Пригласил: <code>{referrer_id or '-'}</code>\n"
        f"\n"
        f"🩸 Покупок: <b>{buys}</b> на <b>{buys_sum:.2f} {CURRENCY}</b>\n"
        f"💀 Продаж: <b>{sells}</b> на <b>{sells_sum:.2f} {CURRENCY}</b>\n"
    )

    kb_rows = [
        [InlineKeyboardButton(text="💰 Начислить коины", callback_data=f"adm_bal_{user_id}"),
         InlineKeyboardButton(text="💸 Списать коины", callback_data=f"adm_balneg_{user_id}")],
    ]
    if bl:
        kb_rows.append([InlineKeyboardButton(text="✅ Убрать из ЧС", callback_data=f"adm_unbl_{user_id}")])
    else:
        kb_rows.append([InlineKeyboardButton(text="🚫 Добавить в ЧС", callback_data=f"adm_bl_{user_id}")])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

# =========================================================
# --- ПРОМОКОДЫ ---
# =========================================================

@dp.callback_query(F.data == "adm_promos")
async def adm_promos(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    cursor.execute("SELECT code, discount, uses_left FROM promos ORDER BY created_at DESC LIMIT 20")
    rows = cursor.fetchall()

    text = "🎟 <b>Промокоды</b>\n━━━━━━━━━━━━━━\n"
    if not rows:
        text += "\n<i>Пусто.</i>"
    else:
        for code, disc, left in rows:
            text += f"  🩸 <code>{code}</code> — {disc}% · осталось: {left}\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="adm_promo_new")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_promo_new")
async def adm_promo_new(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "🎟 <b>Новый промокод</b>\n\n"
        "Введи код (латиница, цифры):\n"
        "<i>Например: KROV2024</i>\n\n"
        "Или напиши <code>RANDOM</code> — сгенерирую сам.",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_promo_code)
    await callback.answer()

@dp.message(AdminState.waiting_for_promo_code)
async def adm_promo_code(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    code = message.text.strip().upper()
    if code == "RANDOM":
        code = gen_promo_code(8)

    cursor.execute("SELECT 1 FROM promos WHERE code=?", (code,))
    if cursor.fetchone():
        await clean_reply(message, f"❌ Код <code>{code}</code> уже существует.",
                          kb=admin_back_kb(), state=state)
        return

    await state.update_data(promo_code=code)
    await clean_reply(message,
        f"🎟 Код: <code>{code}</code>\n\n"
        f"Введи скидку в % (1-99):",
        kb=admin_back_kb(), state=state)
    await state.set_state(AdminState.waiting_for_promo_discount)

@dp.message(AdminState.waiting_for_promo_discount)
async def adm_promo_discount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        disc = int(message.text)
        if not 1 <= disc <= 99:
            raise ValueError()
        await state.update_data(promo_discount=disc)
        await clean_reply(message,
            f"🎟 Скидка: <b>{disc}%</b>\n\n"
            f"Сколько активаций? (число):",
            kb=admin_back_kb(), state=state)
        await state.set_state(AdminState.waiting_for_promo_uses)
    except:
        await clean_reply(message, "❌ Введи число от 1 до 99.", kb=admin_back_kb(), state=state)

@dp.message(AdminState.waiting_for_promo_uses)
async def adm_promo_uses(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        uses = int(message.text)
        if uses <= 0:
            raise ValueError()
        data = await state.get_data()
        code = data["promo_code"]
        disc = data["promo_discount"]

        cursor.execute("INSERT INTO promos (code, discount, uses_left) VALUES (?, ?, ?)",
                       (code, disc, uses))
        db.commit()

        sent = await message.answer(
            f"✅ <b>Промокод создан!</b>\n\n"
            f"  🎟 Код: <code>{code}</code>\n"
            f"  💰 Скидка: <b>{disc}%</b>\n"
            f"  🔢 Активаций: <b>{uses}</b>\n\n"
            f"<i>Игроки вводят: /promo {code}</i>",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()
    except:
        await clean_reply(message, "❌ Введи положительное число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- ПОРОГ СКЛАДА ---
# =========================================================

@dp.callback_query(F.data == "adm_threshold")
async def adm_threshold(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    threshold = float(get_setting("stock_threshold", DEFAULT_STOCK_THRESHOLD))
    stock = get_stock_total()
    alert = get_setting("stock_alert_sent", "0") == "1"

    text = (
        f"📉 <b>Порог склада</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  📊 Текущий склад: <b>{stock:,.0f} N</b>\n"
        f"  📉 Порог уведомления: <b>{threshold:,.0f} N</b>\n"
        f"  🔔 Тревога: <b>{'АКТИВНА' if alert else 'нет'}</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"<i>Когда склад упадёт ниже порога — придёт уведомление.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить порог", callback_data="adm_threshold_edit")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_threshold_edit")
async def adm_threshold_edit(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text(
        "📉 <b>Новый порог склада</b>\n\nВведи число N:",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_threshold)
    await callback.answer()

@dp.message(AdminState.waiting_for_threshold)
async def adm_threshold_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text.replace(" ", ""))
        set_setting("stock_threshold", val)
        sent = await message.answer(
            f"✅ Порог обновлён: <code>{val:,.0f} N</code>",
            reply_markup=admin_menu_kb(), parse_mode="HTML"
        )
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- ПАУЗЫ ---
# =========================================================

@dp.callback_query(F.data == "adm_pause_menu")
async def adm_pause_menu(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return

    p_all = get_setting("paused_all", "0") == "1"
    p_buy = get_setting("paused_buy", "0") == "1"
    p_sell = get_setting("paused_sell", "0") == "1"

    def st(b): return "🔴 Пауза" if b else "🟢 Работает"

    text = (
        f"⏸ <b>Управление паузами</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"  🌙 Весь магазин: <b>{st(p_all)}</b>\n"
        f"  🛒 Покупки: <b>{st(p_buy)}</b>\n"
        f"  💸 Продажи: <b>{st(p_sell)}</b>\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"<i>Когда пауза включена — игроки получают сообщение «закрыто».</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=("▶️ Открыть магазин" if p_all else "🌙 Приостановить весь магазин"),
            callback_data="adm_toggle_all")],
        [InlineKeyboardButton(
            text=("▶️ Возобновить покупки" if p_buy else "⏸ Пауза покупок"),
            callback_data="adm_toggle_buy")],
        [InlineKeyboardButton(
            text=("▶️ Возобновить продажи" if p_sell else "⏸ Пауза продаж"),
            callback_data="adm_toggle_sell")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_toggle_all")
async def adm_toggle_all(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    new = "0" if get_setting("paused_all", "0") == "1" else "1"
    set_setting("paused_all", new)
    await callback.answer("Магазин на паузе." if new == "1" else "Магазин открыт.", show_alert=True)
    await adm_pause_menu(callback, state)

@dp.callback_query(F.data == "adm_toggle_buy")
async def adm_toggle_buy(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    new = "0" if get_setting("paused_buy", "0") == "1" else "1"
    set_setting("paused_buy", new)
    await callback.answer("Покупки на паузе." if new == "1" else "Покупки открыты.", show_alert=True)
    await adm_pause_menu(callback, state)

@dp.callback_query(F.data == "adm_toggle_sell")
async def adm_toggle_sell(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    new = "0" if get_setting("paused_sell", "0") == "1" else "1"
    set_setting("paused_sell", new)
    await callback.answer("Продажи на паузе." if new == "1" else "Продажи открыты.", show_alert=True)
    await adm_pause_menu(callback, state)

# =========================================================
# --- РАССЫЛКА ---
# =========================================================

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    await callback.message.edit_text(
        f"📢 <b>Рассылка</b>\n\nСообщение получат <b>{count}</b> жертв.\n\nОтправь текст (HTML):",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_broadcast)
    await callback.answer()

@dp.message(AdminState.waiting_for_broadcast)
async def adm_broadcast_send(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    text = message.html_text
    try: await message.delete()
    except: pass

    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    sent = 0; failed = 0
    status_msg = await message.answer(f"⏳ Рассылка началась: {len(users)}...")
    for (uid,) in users:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
            await asyncio.sleep(0.05)
        except: failed += 1

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n  📤 Отправлено: <b>{sent}</b>\n  ❌ Ошибок: <b>{failed}</b>",
        parse_mode="HTML"
    )
    LAST_MESSAGES[message.from_user.id] = status_msg.message_id
    await state.clear()

# =========================================================
# --- КАРТА ---
# =========================================================

@dp.callback_query(F.data == "adm_card")
async def adm_card(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    card = get_setting("card_number"); name = get_setting("card_name")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить номер", callback_data="adm_card_num")],
        [InlineKeyboardButton(text="✏️ Изменить получателя", callback_data="adm_card_name")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(
        f"💳 <b>Реквизиты</b>\n━━━━━━━━━━━━━━\n"
        f"  💳 Карта: <code>{card}</code>\n"
        f"  👤 Получатель: <b>{name}</b>",
        reply_markup=kb, parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_card_num")
async def adm_card_num(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text("💳 Новый номер карты:", reply_markup=admin_back_kb())
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_card_number)
    await callback.answer()

@dp.callback_query(F.data == "adm_card_name")
async def adm_card_name(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text("👤 Новое имя получателя:", reply_markup=admin_back_kb())
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_card_name)
    await callback.answer()

@dp.message(AdminState.waiting_for_card_number)
async def adm_card_num_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    set_setting("card_number", message.text.strip())
    sent = await message.answer(f"✅ Карта обновлена: <code>{message.text}</code>",
                                reply_markup=admin_menu_kb(), parse_mode="HTML")
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    try: await message.delete()
    except: pass
    await state.clear()

@dp.message(AdminState.waiting_for_card_name)
async def adm_card_name_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    set_setting("card_name", message.text.strip())
    sent = await message.answer(f"✅ Получатель: <b>{message.text}</b>",
                                reply_markup=admin_menu_kb(), parse_mode="HTML")
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    try: await message.delete()
    except: pass
    await state.clear()

# =========================================================
# --- КУРСЫ ---
# =========================================================

@dp.callback_query(F.data == "adm_prices")
async def adm_prices(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    sell = get_setting("price_sell"); buy = get_setting("price_buy")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Курс продажи", callback_data="adm_price_sell")],
        [InlineKeyboardButton(text="✏️ Курс скупки", callback_data="adm_price_buy")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_back")]
    ])
    await callback.message.edit_text(
        f"💱 <b>Курсы</b>\n━━━━━━━━━━━━━━\n"
        f"  🔴 Продажа: <b>{sell} {CURRENCY}</b> / 1кк\n"
        f"  🟢 Скупка:  <b>{buy} {CURRENCY}</b> / 1кк",
        reply_markup=kb, parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await callback.answer()

@dp.callback_query(F.data == "adm_price_sell")
async def adm_price_sell(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text("🔴 Новый курс ПРОДАЖИ:", reply_markup=admin_back_kb())
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_price_sell)
    await callback.answer()

@dp.callback_query(F.data == "adm_price_buy")
async def adm_price_buy(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(); return
    await callback.message.edit_text("🟢 Новый курс СКУПКИ:", reply_markup=admin_back_kb())
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_price_buy)
    await callback.answer()

@dp.message(AdminState.waiting_for_price_sell)
async def adm_price_sell_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text)
        set_setting("price_sell", val)
        sent = await message.answer(f"✅ Курс продажи: <b>{val} {CURRENCY}</b>",
                                    reply_markup=admin_menu_kb(), parse_mode="HTML")
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

@dp.message(AdminState.waiting_for_price_buy)
async def adm_price_buy_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    try:
        val = float(message.text)
        set_setting("price_buy", val)
        sent = await message.answer(f"✅ Курс скупки: <b>{val} {CURRENCY}</b>",
                                    reply_markup=admin_menu_kb(), parse_mode="HTML")
        LAST_MESSAGES[message.from_user.id] = sent.message_id
        try: await message.delete()
        except: pass
        await state.clear()
    except:
        await clean_reply(message, "❌ Введи число.", kb=admin_back_kb(), state=state)

# =========================================================
# --- ПОДДЕРЖКА ---
# =========================================================

@dp.callback_query(F.data == "adm_support")
async def adm_support(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    sup = get_setting("support")
    await callback.message.edit_text(
        f"👨‍💻 Текущий контакт: {sup}\n\nВведи новый (например @new_support):",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_support)
    await callback.answer()

@dp.message(AdminState.waiting_for_support)
async def adm_support_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    set_setting("support", message.text.strip())
    sent = await message.answer(f"✅ Поддержка: <b>{message.text}</b>",
                                reply_markup=admin_menu_kb(), parse_mode="HTML")
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    try: await message.delete()
    except: pass
    await state.clear()

# =========================================================
# --- ИГРОВОЙ ID ---
# =========================================================

@dp.callback_query(F.data == "adm_gameid")
async def adm_gameid(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа.", show_alert=True); return
    gid = get_setting("game_id", DEFAULT_GAME_ID)
    await callback.message.edit_text(
        f"🎮 Текущий ID магазина: <code>{gid}</code>\n\nВведи новый:",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    LAST_MESSAGES[callback.from_user.id] = callback.message.message_id
    await state.set_state(AdminState.waiting_for_game_id)
    await callback.answer()

@dp.message(AdminState.waiting_for_game_id)
async def adm_gameid_save(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.delete(); return
    set_setting("game_id", message.text.strip())
    sent = await message.answer(f"✅ Game ID: <code>{message.text}</code>",
                                reply_markup=admin_menu_kb(), parse_mode="HTML")
    LAST_MESSAGES[message.from_user.id] = sent.message_id
    try: await message.delete()
    except: pass
    await state.clear()

# =========================================================
# --- ЕЖЕДНЕВНЫЙ ОТЧЁТ В 19:00 МСК ---
# =========================================================

async def daily_report_task():
    while True:
        try:
            now = datetime.utcnow() + timedelta(hours=3)
            target = now.replace(hour=19, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)

            since = (datetime.utcnow() + timedelta(hours=3) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute(
                "SELECT COUNT(*), SUM(total_price) FROM orders WHERE status='completed' AND created_at >= ?",
                (since,))
            c_count, c_sum = cursor.fetchone(); c_count = c_count or 0; c_sum = c_sum or 0

            cursor.execute(
                "SELECT COUNT(*), SUM(total_price) FROM orders WHERE status='sell_completed' AND created_at >= ?",
                (since,))
            s_count, s_sum = cursor.fetchone(); s_count = s_count or 0; s_sum = abs(s_sum or 0)

            stock = get_stock_total()
            bank = get_bank()
            profit = c_sum - s_sum
            p_emoji = "📈" if profit >= 0 else "📉"

            report = (
                f"🩸 <b>ЕЖЕДНЕВНЫЙ ОТЧЁТ</b>\n"
                f"☠️ <i>Imperator Shop · {now.strftime('%d.%m.%Y')} 19:00 МСК</i>\n"
                f"━━━━━━━━━━━━━━\n"
                f"☠️ ─────  <b>ЗА СУТКИ</b>  ───── ☠️\n"
                f"  ✅ Продано: <b>{c_count}</b> · <b>+{c_sum:.2f} {CURRENCY}</b>\n"
                f"  🩸 Куплено: <b>{s_count}</b> · <b>-{s_sum:.2f} {CURRENCY}</b>\n"
                f"  {p_emoji} Прибыль: <b>{profit:+.2f} {CURRENCY}</b>\n\n"
                f"🩸 ─────  <b>СЕЙЧАС</b>  ───── 🩸\n"
                f"  📦 Склад: <b>{stock:,.0f} N</b>\n"
                f"  🏦 Банк: <b>{bank:.2f} {CURRENCY}</b>\n"
                f"━━━━━━━━━━━━━━\n"
                f"<i>Останься живым. 🩸</i>"
            )

            try:
                await bot.send_message(ADMIN_ID, report, parse_mode="HTML")
            except: pass

        except Exception as e:
            print(f"Ошибка в daily_report_task: {e}")
            await asyncio.sleep(60)

# =========================================================
# --- ЗАПУСК ---
# =========================================================

async def main():
    logging.basicConfig(level=logging.INFO)
    print("🩸 Imperator Shop запущен...")
    asyncio.create_task(daily_report_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())