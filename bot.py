#!/usr/bin/env python3
import os
import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from collections import defaultdict

import asyncpg
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.utils.callback_data import CallbackData

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
ADMINS = [int(x.strip()) for x in os.getenv('ADMINS', '').split(',') if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL muhit o'zgaruvchilari sozlanmagan")

# --- Bot & Dispatcher ---
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB pool ---
db_pool: asyncpg.pool.Pool | None = None

async def init_db_pool():
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            quantity INT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now()
        );
        ''')
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            product_id INT REFERENCES products(id),
            quantity INT NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            total NUMERIC(14,2) NOT NULL,
            client_name TEXT,
            client_phone TEXT,
            payment_method TEXT,
            sale_date TIMESTAMP NOT NULL,
            seller_id BIGINT
        );
        ''')
    logger.info("DB tayyor")
    return pool

# --- Helpers ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

# --- Callback Data ---
cart_cb = CallbackData("cart", "action", "product_id")

# --- FSM ---
class AddProductStates(StatesGroup):
    waiting_for_input = State()

# --- Keyboards ---
def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Mahsulot qo‘shish")],
            [KeyboardButton("🛒 Sotish")],
            [KeyboardButton("📊 Hisobot")],
        ],
        resize_keyboard=True
    )

def cart_inline_kb(product_id: int):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(
        text="➕ Savatchaga qo‘shish",
        callback_data=cart_cb.new(action="add", product_id=product_id)
    ))
    return kb

def checkout_kb():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(text="✅ Checkout", callback_data="checkout"))
    return kb

# --- Memory cart per user ---
user_cart = defaultdict(list)  # user_id: list of dict {product_id, name, price, quantity}

# --- Handlers ---
@dp.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda bu botdan foydalanish huquqi yo'q.")
        return
    await state.clear()
    await message.answer(
        "Salom! CRM botga hush kelibsiz.\n"
        "➕ Mahsulot qo'shish — yangi mahsulot qo'shish\n"
        "🛒 Sotish — mahsulot qidirib sotish\n"
        "📊 Hisobot — kun/oy/yil bo'yicha hisobot",
        reply_markup=main_menu_kb()
    )

# --- Add Product ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Mahsulotni yuboring: nomi, miqdori, narxi\nMisol: Olma, 10, 5000")

@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
    parts = [p.strip() for p in message.text.strip().split(',')]
    if len(parts) != 3:
        await message.answer("Format noto'g'ri. Misol: Olma, 10, 5000")
        return
    try:
        name = parts[0]
        qty = int(parts[1])
        price = Decimal(parts[2].replace(" ", ""))
    except Exception:
        await message.answer("Xatolik: ma'lumotlarni tekshiring.")
        return

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if row:
            await conn.execute(
                "UPDATE products SET quantity = quantity+$1, price=$2, updated_at=now() WHERE id=$3",
                qty, price, row['id']
            )
            await message.answer(f"✅ {name} yangilandi: +{qty}, narxi: {price}")
        else:
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)",
                name, qty, price
            )
            await message.answer(f"✅ Yangi mahsulot qo'shildi: {name}, {qty} ta, {price}")
    await state.clear()

# --- Sell / Search ---
@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message):
    await message.answer("Mahsulot nomini yozing (qidirish):")

@dp.message()
async def sell_search(message: types.Message):
    query = message.text.strip().lower()
    if not query:
        await message.answer("Iltimos, mahsulot nomini kiriting.")
        return
    rows = await db_pool.fetch(
        "SELECT id, name, quantity, price FROM products WHERE LOWER(name) LIKE $1 ORDER BY name LIMIT 10",
        f"%{query}%"
    )
    if not rows:
        await message.answer("Mahsulot topilmadi.")
        return
    for r in rows:
        await message.answer(
            f"{r['name']} — {r['quantity']} ta, narxi: {r['price']}",
            reply_markup=cart_inline_kb(r['id'])
        )

# --- Cart Callback ---
@dp.callback_query(cart_cb.filter())
async def handle_cart(call: types.CallbackQuery, callback_data: dict):
    product_id = int(callback_data["product_id"])
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, name, quantity, price FROM products WHERE id=$1", product_id)
        if not row or row["quantity"] <= 0:
            return await call.answer("Omborda mahsulot qolmagan", show_alert=True)
    # Add to memory cart
    found = False
    for item in user_cart[call.from_user.id]:
        if item['product_id'] == product_id:
            item['quantity'] += 1
            found = True
            break
    if not found:
        user_cart[call.from_user.id].append({
            'product_id': product_id,
            'name': row['name'],
            'price': row['price'],
            'quantity': 1
        })
    await call.answer(f"✅ {row['name']} savatchaga qo‘shildi")

# --- Checkout ---
@dp.callback_query(Text("checkout"))
async def handle_checkout(call: types.CallbackQuery):
    cart_items = user_cart.get(call.from_user.id)
    if not cart_items:
        return await call.answer("Savatcha bo'sh", show_alert=True)
    total_sum = sum(item['price'] * item['quantity'] for item in cart_items)
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            for item in cart_items:
                # Check quantity
                prod = await conn.fetchrow("SELECT quantity FROM products WHERE id=$1", item['product_id'])
                if not prod or prod['quantity'] < item['quantity']:
                    return await call.answer(f"{item['name']} yetarli miqdorda mavjud emas", show_alert=True)
            # Deduct quantity and insert sales
            for item in cart_items:
                await conn.execute(
                    "INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id) "
                    "VALUES($1,$2,$3,$4,$5,$6)",
                    item['product_id'], item['quantity'], item['price'], item['price']*item['quantity'],
                    datetime.utcnow(), call.from_user.id
                )
                await conn.execute(
                    "UPDATE products SET quantity = quantity - $1, updated_at=now() WHERE id=$2",
                    item['quantity'], item['product_id']
                )
    user_cart[call.from_user.id] = []
    await call.message.answer(f"✅ Checkout amalga oshirildi. Jami: {total_sum}")
    await call.answer()

# --- Hisobot ---
@dp.message(Text("📊 Hisobot"))
async def stats_handler(message: types.Message):
    try:
        async with db_pool.acquire() as conn:
            total = await conn.fetchval('SELECT COALESCE(SUM(total),0) FROM sales')
            today = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date)=CURRENT_DATE")
            month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date)=DATE_TRUNC('month', CURRENT_DATE)")
            year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date)=DATE_TRUNC('year', CURRENT_DATE)")
            my_total = await conn.fetchval('SELECT COALESCE(SUM(total),0) FROM sales WHERE seller_id=$1', message.from_user.id)
    except Exception:
        logger.exception('Stats DB error')
        return await message.answer('Statistikani olishda xatolik yuz berdi')

    text = (
        f"📊 Savdo statistikasi:\n\n"
        f"💰 Umumiy daromad: {total}\n"
        f"📅 Bugungi: {today}\n"
        f"🗓 Oylik: {month}\n"
        f"📆 Yillik: {year}\n\n"
        f"👤 Sizning umumiy savdolaringiz: {my_total}"
    )
    await message.answer(text)

# --- Run ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
