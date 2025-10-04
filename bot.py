#!/usr/bin/env python3
# oddiy crm bot (savatchasiz)

import os
import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [int(x.strip()) for x in os.getenv('ADMINS', '').split(',') if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL sozlanmagan")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool: asyncpg.pool.Pool | None = None

# --- DB init ---
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
            sale_date TIMESTAMP NOT NULL,
            seller_id BIGINT
        );
        ''')
    logger.info("DB pool tayyor")
    return pool

# --- Helpers ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS if ADMINS else False

# --- FSM ---
class AddProductStates(StatesGroup):
    waiting_for_input = State()

# --- Keyboards ---
def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Mahsulot qo‘shish")],
            [KeyboardButton(text="🛒 Sotish")],
            [KeyboardButton(text="📊 Hisobot")],
        ],
        resize_keyboard=True
    )

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda huquq yo‘q")
    await state.clear()
    await message.answer("Salom! CRM bot ishga tayyor.", reply_markup=main_menu_kb())

# --- Add product ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Ruxsat yo‘q")
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Mahsulot: nomi, miqdori, narxi\nMisol: Olma, 10, 5000")

@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
    parts = [p.strip() for p in message.text.split(",")]
    try:
        name, qty, price = parts[0], int(parts[1]), Decimal(parts[2])
    except Exception:
        return await message.answer("❌ Format noto‘g‘ri. Misol: Olma, 10, 5000")

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if row:
            await conn.execute("UPDATE products SET quantity=quantity+$1, price=$2, updated_at=now() WHERE id=$3",
                               qty, price, row["id"])
            await message.answer(f"✅ {name} yangilandi (+{qty} dona, narx {price})")
        else:
            await conn.execute("INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)", name, qty, price)
            await message.answer(f"✅ Yangi mahsulot qo‘shildi: {name}, {qty} dona, {price}")
    await state.clear()

# --- Sotish ---
@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Ruxsat yo‘q")
    await message.answer("Mahsulot nomini yozing (qidiruv):")

@dp.message()
async def sell_search(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    q = message.text.strip().lower()
    if not q:
        return
    rows = await db_pool.fetch("SELECT id, name, quantity, price FROM products WHERE LOWER(name) LIKE $1 LIMIT 5", f"%{q}%")
    if not rows:
        return await message.answer("❌ Mahsulot topilmadi.")
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Sotib olish ({r['price']})", callback_data=f"buy:{r['id']}")]
        ])
        await message.answer(f"{r['name']} — {r['quantity']} ta, narxi {r['price']}", reply_markup=kb)

# --- Callback sotib olish ---
@dp.callback_query(F.data.startswith("buy:"))
async def handle_buy(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer("⛔ Ruxsat yo‘q", show_alert=True)
    product_id = int(call.data.split(":")[1])
    row = await db_pool.fetchrow("SELECT id, name, quantity, price FROM products WHERE id=$1", product_id)
    if not row:
        return await call.answer("❌ Topilmadi", show_alert=True)
    if row["quantity"] <= 0:
        return await call.answer("❌ Omborda qolmagan", show_alert=True)

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id) VALUES($1,$2,$3,$4,$5,$6)",
                           row["id"], 1, row["price"], row["price"], datetime.utcnow(), call.from_user.id)
        await conn.execute("UPDATE products SET quantity=quantity-1, updated_at=now() WHERE id=$1", row["id"])

    await call.message.edit_text(f"✅ {row['name']} sotildi!\nQolgan: {row['quantity']-1} ta")
    await call.answer("✅ Sotildi")

# --- Hisobot ---
@dp.message(Text("📊 Hisobot"))
async def stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales")
    await message.answer(f"📊 Umumiy savdo: {total}")

# --- Run ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to‘xtatildi")
