#!/usr/bin/env python3
# bot.py

import os
import asyncio
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
import hashlib
import asyncpg

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    CallbackQuery
)

load_dotenv()

# Muhit o‘zgaruvchilari
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL sozlanmagan")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB helpers ---
async def init_db_pool():
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            quantity INT NOT NULL,
            price NUMERIC(10,2) NOT NULL
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            product_id INT REFERENCES products(id),
            quantity INT NOT NULL,
            price NUMERIC(10,2) NOT NULL,
            total NUMERIC(10,2) NOT NULL,
            client_name TEXT,
            client_phone TEXT,
            payment_method TEXT,
            sale_date TIMESTAMP NOT NULL
        );
        """)
    return pool

db_pool: asyncpg.pool.Pool | None = None

# --- FSM States ---
class AddProductStates(StatesGroup):
    waiting_for_input = State()

# --- Keyboard ---
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo‘shish")],
        [KeyboardButton(text="🛒 Sotish")],
        [KeyboardButton(text="📊 Hisobot")],
    ], resize_keyboard=True)
    return kb

# --- Start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "Salom! Bu oddiy CRM bot.\n\n"
        "Buyruqlar:\n"
        "➕ Mahsulot qo‘shish — yangi mahsulot kiritish\n"
        "🛒 Sotish — mahsulot sotish\n"
        "📊 Hisobot — umumiy daromad ko‘rish\n\n"
        "Inline qidiruv: yozuv maydonida `@BotNomi <so‘z>` deb yozing."
    )
    await message.answer(text, reply_markup=main_menu_kb())

# --- Mahsulot qo‘shish ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Mahsulotni shu formatda yuboring: nomi, soni, narxi\n\nMasalan: `Olma, 10, 5000`", parse_mode="Markdown")

@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
    text = message.text.strip()
    parts = [p.strip() for p in text.split(",")]
    try:
        if len(parts) == 3:
            name = parts[0]
            qty = int(parts[1])
            price = Decimal(parts[2])
        else:
            await message.answer("❌ Format noto‘g‘ri. Qaytadan kiriting (masalan: Olma, 10, 5000).")
            return
    except Exception:
        await message.answer("❌ Ma‘lumotlarda xatolik. Qaytadan urinib ko‘ring.")
        return

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if product:
            await conn.execute(
                "UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3",
                qty, price, product['id']
            )
            await message.answer(f"🔄 {name} yangilandi: +{qty} dona, narxi {price} so‘m")
        else:
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1, $2, $3)",
                name, qty, price
            )
            await message.answer(f"✅ Yangi mahsulot qo‘shildi: {name}, {qty} dona, {price} so‘m")
    await state.clear()

# --- Hisobot ---
@dp.message(Text("📊 Hisobot"))
async def show_stats(message: types.Message):
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total), 0) FROM sales")
    await message.answer(f"📊 Umumiy daromad: {total} so‘m")

# --- Sotish (inline qidiruv orqali) ---
@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message):
    await message.answer("🔍 Mahsulotni qidirish uchun yozuv maydonida `@BotNomi <nomi>` deb yozing.\n\nMasalan: `@BotNomi Olma`")

# 🔍 Inline qidiruv
@dp.inline_query()
async def inline_search(query: InlineQuery):
    text = query.query.strip()
    results = []

    if not text:
        return await query.answer([], cache_time=1)

    products = await db_pool.fetch(
        "SELECT id, name, quantity, price FROM products WHERE name ILIKE $1 LIMIT 10",
        f"%{text}%"
    )

    for product in products:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"🛒 Sotib olish ({product['price']} so‘m)",
                callback_data=f"buy:{product['id']}"
            )]
        ])

        results.append(
            InlineQueryResultArticle(
                id=str(product['id']),
                title=product['name'],
                description=f"Soni: {product['quantity']} | Narxi: {product['price']} so‘m",
                input_message_content=InputTextMessageContent(
                    message_text=f"{product['name']} — {product['price']} so‘m (Qolgan: {product['quantity']} dona)"
                ),
                reply_markup=kb
            )
        )

    await query.answer(results, cache_time=1)

# 🛒 Sotib olish tugmasi
@dp.callback_query(F.data.startswith("buy:"))
async def handle_buy(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])

    product = await db_pool.fetchrow(
        "SELECT name, quantity, price FROM products WHERE id=$1",
        product_id
    )

    if not product:
        return await call.answer("❌ Mahsulot topilmadi", show_alert=True)

    if product['quantity'] <= 0:
        return await call.answer("❌ Omborda qolmagan", show_alert=True)

    # 1 dona kamaytirish
    await db_pool.execute(
        "UPDATE products SET quantity = quantity - 1 WHERE id=$1",
        product_id
    )

    # Sotuv jadvaliga yozish
    await db_pool.execute(
        "INSERT INTO sales(product_id, quantity, price, total, sale_date) VALUES ($1, $2, $3, $4, $5)",
        product_id, 1, product['price'], product['price'], datetime.now()
    )

    await call.message.edit_text(
        f"✅ {product['name']} sotildi!\nQolgan: {product['quantity'] - 1} ta"
    )
    await call.answer("✅ Sotib olindi")

# --- Run ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    print("Bot ishga tushdi 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
