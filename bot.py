#!/usr/bin/env python3
# bot.py

import os
import asyncio
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
import hashlib
import asyncpg
import matplotlib.pyplot as plt
import io
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile
)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL sozlanmagan!")

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

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
            sale_date TIMESTAMP NOT NULL,
            seller_id BIGINT
        );
        """)
    return pool

db_pool: asyncpg.pool.Pool | None = None

# --- FSM states ---
class AddProductStates(StatesGroup):
    waiting_for_input = State()

class SellStates(StatesGroup):
    waiting_for_product = State()
    waiting_for_quantity = State()
    waiting_for_client_name = State()
    waiting_for_client_phone = State()
    waiting_for_payment = State()
    confirm = State()

# --- Klaviaturalar ---
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo‘shish")],
        [KeyboardButton(text="🛒 Mahsulot sotish")],
        [KeyboardButton(text="📊 Statistika")],
    ], resize_keyboard=True)
    return kb

def payment_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💵 Naqd"), KeyboardButton(text="💳 Karta")],
        [KeyboardButton(text="📅 Qarzga")],
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

# --- Start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda bu botdan foydalanish huquqi yo‘q.")
        return

    await state.clear()
    text = (
        "Salom! Men oddiy CRM-botman.\n\n"
        "Mavjud buyruqlar:\n"
        "➕ Mahsulot qo‘shish — yangi mahsulot qo‘shish yoki mavjudini yangilash\n"
        "🛒 Mahsulot sotish — savdo qilish\n"
        "📊 Statistika — umumiy daromadni ko‘rish\n\n"
        "Misol: `Olma, 10, 15000`\n\n"
        "Tezkor qidiruv uchun chatda yozing:\n"
        "`@SizningBotingiz mahsulot nomi`"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="Markdown")

# --- Mahsulot qo‘shish ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Mahsulotni shu formatda yuboring: nomi, miqdori, narxi\n\nMisol: Olma, 10, 15000")

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
            await message.answer("Format noto‘g‘ri. Misol: Olma, 10, 15000")
            return
    except Exception:
        await message.answer("Xatolik! Qaytadan urinib ko‘ring.")
        return

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if product:
            await conn.execute(
                "UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3",
                qty, price, product['id']
            )
            await message.answer(f"✅ Mahsulot yangilandi: {name}, +{qty} dona, narx: {price}")
        else:
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)",
                name, qty, price
            )
            await message.answer(f"✅ Yangi mahsulot qo‘shildi: {name}, miqdor: {qty}, narx: {price}")
    await state.clear()

# --- Statistika ---
@dp.message(Text("📊 Statistika"))
async def show_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales")
        today = await conn.fetchval(
            "SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date)=CURRENT_DATE"
        )
        month = await conn.fetchval(
            "SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date)=DATE_TRUNC('month', CURRENT_DATE)"
        )
        year = await conn.fetchval(
            "SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date)=DATE_TRUNC('year', CURRENT_DATE)"
        )

    text = (
        "📊 *Savdo statistikasi*\n\n"
        f"💰 Umumiy daromad: {total}\n"
        f"📅 Bugungi savdolar: {today}\n"
        f"🗓 Oylik savdolar: {month}\n"
        f"📆 Yillik savdolar: {year}"
    )
    await message.answer(text, parse_mode="Markdown")

    # Grafik chizish
    labels = ["Bugungi", "Oylik", "Yillik"]
    values = [float(today), float(month), float(year)]

    plt.figure(figsize=(6, 4))
    bars = plt.bar(labels, values)
    plt.title("📊 Savdo Statistikasi")
    plt.xlabel("Davr")
    plt.ylabel("Summa")
    for bar in bars:
        h = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, h, f"{h:.2f}", ha='center', va='bottom')

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()

    photo = FSInputFile(buf, filename="stats.png")
    await message.answer_photo(photo, caption="📊 Grafik ko‘rinishida")

# --- Botni ishga tushirish ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    print("Bot ishga tushdi 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
