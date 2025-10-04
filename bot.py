#!/usr/bin/env python3
import os
import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL muhit o'zgaruvchilari sozlanmagan")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool = None
cart = {}  # {user_id: {product_id: qty}}

# --- DB Init ---
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                quantity INT NOT NULL,
                price NUMERIC(12,2) NOT NULL
            );
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                product_id INT REFERENCES products(id),
                quantity INT NOT NULL,
                price NUMERIC(12,2) NOT NULL,
                total NUMERIC(14,2) NOT NULL,
                sale_date TIMESTAMP NOT NULL,
                seller_id BIGINT
            );
        ''')

# --- Helpers ---
def is_admin(user_id: int):
    return user_id in ADMINS

def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton("➕ Mahsulot qo‘shish")],
            [KeyboardButton("🛒 Sotish")],
            [KeyboardButton("📊 Hisobot")],
        ], resize_keyboard=True
    )

# --- Handlers ---
@dp.message(Command("start"))
async def start(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda ruxsat yo'q")
    await message.answer("Salom! CRM botga hush kelibsiz", reply_markup=main_menu_kb())

# --- Add Product ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def add_product(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda ruxsat yo'q")
    await message.answer("Format: Mahsulot nomi, miqdori, narxi\nMisol: Olma, 10, 5000")

@dp.message()
async def process_add_product(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) != 3:
        return await message.answer("Format noto'g'ri")
    try:
        name, qty, price = parts[0], int(parts[1]), Decimal(parts[2])
    except:
        return await message.answer("Xato: miqdor yoki narx noto'g'ri")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if row:
            await conn.execute("UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3", qty, price, row['id'])
        else:
            await conn.execute("INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)", name, qty, price)
    await message.answer(f"✅ {name} saqlandi!")

# --- Sell Product ---
@dp.message(Text("🛒 Sotish"))
async def sell(message: types.Message):
    await message.answer("Mahsulot nomini kiriting:")

@dp.message()
async def search_product(message: types.Message):
    q = message.text.lower()
    rows = await db_pool.fetch("SELECT * FROM products WHERE LOWER(name) LIKE $1 LIMIT 10", f"%{q}%")
    if not rows:
        return await message.answer("Mahsulot topilmadi")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(f"➕ Savatchaga ({r['price']})", callback_data=f"addcart:{r['id']}")]
        ])
        await message.answer(f"{r['name']} — {r['quantity']} ta, narxi: {r['price']}", reply_markup=kb)

@dp.callback_query(F.data.startswith("addcart:"))
async def add_cart(call: types.CallbackQuery):
    user_id = call.from_user.id
    pid = int(call.data.split(":")[1])
    if user_id not in cart:
        cart[user_id] = {}
    cart[user_id][pid] = cart[user_id].get(pid, 0) + 1
    await call.answer("Savatchaga qo'shildi!")

@dp.message(Text("🛒 Savatchani ko‘rish"))
async def view_cart(message: types.Message):
    user_id = message.from_user.id
    if user_id not in cart or not cart[user_id]:
        return await message.answer("Savatcha bo'sh")
    text = ""
    total = Decimal(0)
    async with db_pool.acquire() as conn:
        for pid, qty in cart[user_id].items():
            row = await conn.fetchrow("SELECT name, price, quantity FROM products WHERE id=$1", pid)
            if not row:
                continue
            item_total = row['price'] * qty
            text += f"{row['name']} — {qty} ta, narxi: {row['price']} = {item_total}\n"
            total += item_total
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("✅ Xaridni tasdiqlash", callback_data="checkout")],
        [InlineKeyboardButton("❌ Bo'shatish", callback_data="clearcart")]
    ])
    await message.answer(f"{text}\n💰 Jami: {total}", reply_markup=kb)

@dp.callback_query(F.data=="clearcart")
async def clear_cart(call: types.CallbackQuery):
    cart[call.from_user.id] = {}
    await call.message.edit_text("Savatcha bo'shatildi")

@dp.callback_query(F.data=="checkout")
async def checkout(call: types.CallbackQuery):
    user_id = call.from_user.id
    if user_id not in cart or not cart[user_id]:
        return await call.answer("Savatcha bo'sh", show_alert=True)
    async with db_pool.acquire() as conn:
        for pid, qty in cart[user_id].items():
            row = await conn.fetchrow("SELECT quantity, price FROM products WHERE id=$1", pid)
            if row['quantity'] < qty:
                continue
            await conn.execute("INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id) VALUES($1,$2,$3,$4,$5,$6)",
                               pid, qty, row['price'], row['price']*qty, datetime.utcnow(), user_id)
            await conn.execute("UPDATE products SET quantity=quantity-$1 WHERE id=$2", qty, pid)
    cart[user_id] = {}
    await call.message.edit_text("✅ Xarid tasdiqlandi!")

# --- Run ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
