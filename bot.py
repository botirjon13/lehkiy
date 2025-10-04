#!/usr/bin/env python3

# bot_working.py

# Ishlaydigan CRM bot — o'zbekcha. Aiogram 3.x bilan mos.

import os
import io
import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timezone
from dotenv import load_dotenv

import asyncpg
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
KeyboardButton, ReplyKeyboardMarkup,
InlineKeyboardMarkup, InlineKeyboardButton,
FSInputFile
)

# --- Logging ---

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL muhit o'zgaruvchilari sozlanmagan")

# --- Bot & Dispatcher ---

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB pool ---

db_pool: asyncpg.pool.Pool | None = None

async def init_db_pool():
try:
pool = await asyncpg.create_pool(DATABASE_URL)
async with pool.acquire() as conn:
# kerakli ustunlarni yaratish (mavjud bo'lsa saqlaydi)
await conn.execute(
"""
CREATE TABLE IF NOT EXISTS products (
id SERIAL PRIMARY KEY,
name TEXT UNIQUE NOT NULL,
quantity INT NOT NULL DEFAULT 0,
price NUMERIC(12,2) NOT NULL DEFAULT 0,
created_at TIMESTAMP DEFAULT now(),
updated_at TIMESTAMP DEFAULT now()
);
"""
)
await conn.execute(
"""
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
"""
)
logger.info("DB pool tayyor")
return pool
except Exception:
logger.exception("DB pool yaratishda xato")
raise

# --- Helpers ---

def is_admin(user_id: int) -> bool:
if not ADMINS:
return False
return user_id in ADMINS

# --- FSM states ---

class AddProductStates(StatesGroup):
waiting_for_input = State()

class SellStates(StatesGroup):
waiting_for_query = State()

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

def payment_kb():
return ReplyKeyboardMarkup(
keyboard=[
[KeyboardButton(text="💵 Naqd"), KeyboardButton(text="💳 Karta")],
[KeyboardButton(text="📅 Qarzga")],
],
resize_keyboard=True,
one_time_keyboard=True
)

# --- Handlers ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
if not is_admin(message.from_user.id):
await message.answer("⛔ Sizda bu botdan foydalanish huquqi yo'q.")
return
await state.clear()
await message.answer(
"Salom! CRM bot ishga tayyor.\n\n"
"➕ Mahsulot qo‘shish — yangi mahsulot qo‘shish yoki yangilash\n"
"🛒 Sotish — mahsulotni qidirib sotish va sotish\n"
"📊 Hisobot — kun/oy/yil bo‘yicha hisobot\n\n"
"Misol: mahsulot qo‘shish uchun: `Olma, 10, 5000`",
reply_markup=main_menu_kb()
)

# --- Add product ---

@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
if not is_admin(message.from_user.id):
await message.answer("⛔ Sizda ruxsat yo'q.")
return
await state.set_state(AddProductStates.waiting_for_input)
await message.answer("Mahsulotni yuboring: nomi, miqdori, narxi\nMisol: Olma, 10, 5000")

@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
text = message.text.strip()
parts = [p.strip() for p in text.split(",")]
try:
if len(parts) != 3:
raise ValueError("Format noto'g'ri")
name = parts[0]
qty = int(parts[1])
price = Decimal(parts[2].replace(" ", ""))
except Exception:
await message.answer("❌ Format notoʻgʻri. Misol: Olma, 10, 5000")
return

```
try:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if row:
            await conn.execute(
                "UPDATE products SET quantity = quantity + $1, price = $2, updated_at = now() WHERE id = $3",
                qty, price, row["id"]
            )
            await message.answer(f"✅ {name} yangilandi: +{qty}, narxi: {price}")
        else:
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)",
                name, qty, price
            )
            await message.answer(f"✅ Yangi mahsulot qoʻshildi: {name}, {qty} dona, {price}")
except Exception:
    logger.exception("Add product DB error")
    await message.answer("⚠️ Bazaga yozishda xatolik yuz berdi.")
finally:
    await state.clear()
```

# --- Start selling (set state) ---

@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message, state: FSMContext):
if not is_admin(message.from_user.id):
await message.answer("⛔ Sizda ruxsat yo'q.")
return
await state.set_state(SellStates.waiting_for_query)
await message.answer("Mahsulot nomini kiriting (qidiruv):")

# --- Search handler (only in sell state) ---

@dp.message(SellStates.waiting_for_query)
async def sell_search(message: types.Message, state: FSMContext):
q = message.text.strip()
if not q:
await message.answer("Iltimos, mahsulot nomini yozing.")
return

```
try:
    rows = await db_pool.fetch(
        "SELECT id, name, quantity, price FROM products WHERE LOWER(name) LIKE $1 ORDER BY name LIMIT 10",
        f"%{q.lower()}%"
    )
except Exception:
    logger.exception("DB error on search")
    await message.answer("⚠️ Bazadan ma'lumot olishda xatolik yuz berdi.")
    await state.clear()
    return

if not rows:
    await message.answer("🔎 Mahsulot topilmadi.")
    await state.clear()
    return

for r in rows:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Sotib olish ({r['price']})", callback_data=f"buy:{r['id']}")]
        ]
    )
    await message.answer(f"{r['name']} — {r['quantity']} ta, narxi: {r['price']}", reply_markup=kb)

# clear state so normal flow continues (or keep if you want multiple searches)
await state.clear()
```

# --- Buy callback ---

@dp.callback_query(F.data.startswith("buy:"))
async def handle_buy(call: types.CallbackQuery):
# prevent non-admin usage
if not is_admin(call.from_user.id):
await call.answer("⛔ Sizda ruxsat yo'q.", show_alert=True)
return

```
# parse id
try:
    product_id = int(call.data.split(":", 1)[1])
except Exception:
    await call.answer("Noto'g'ri ma'lumot", show_alert=True)
    return

# load product
try:
    row = await db_pool.fetchrow("SELECT id, name, quantity, price FROM products WHERE id=$1", product_id)
except Exception:
    logger.exception("DB error on fetch product")
    await call.answer("⚠️ Xatolik yuz berdi", show_alert=True)
    return

if not row:
    await call.answer("❌ Mahsulot topilmadi", show_alert=True)
    return

if row["quantity"] <= 0:
    await call.answer("❌ Omborda mahsulot qolmagan", show_alert=True)
    return

# perform transaction: insert sale, decrement stock
try:
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id) VALUES($1,$2,$3,$4,$5,$6)",
                row["id"], 1, row["price"], row["price"], datetime.now(timezone.utc), call.from_user.id
            )
            await conn.execute(
                "UPDATE products SET quantity = quantity - 1, updated_at = now() WHERE id = $1",
                row["id"]
            )
except Exception:
    logger.exception("DB error on buy/transaction")
    # ensure Telegram client loading indicator is removed
    await call.answer("⚠️ Savdoni saqlashda xatolik yuz berdi", show_alert=True)
    return

# Notify — try edit message, fallback to send new message
remaining = row["quantity"] - 1
try:
    await call.message.edit_text(f"✅ {row['name']} sotildi!\nQolgan: {remaining} ta")
except Exception:
    try:
        await call.message.answer(f"✅ {row['name']} sotildi!\nQolgan: {remaining} ta")
    except Exception:
        logger.exception("Failed to send confirmation message")

# Stop loading spinner in client
await call.answer("✅ Sotib olindi")
```

# --- Stats handler ---

@dp.message(Text("📊 Hisobot"))
async def stats_handler(message: types.Message):
if not is_admin(message.from_user.id):
await message.answer("⛔ Sizda ruxsat yo'q.")
return

```
try:
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales")
        today = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date) = CURRENT_DATE")
        month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date) = DATE_TRUNC('month', CURRENT_DATE)")
        year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date) = DATE_TRUNC('year', CURRENT_DATE)")
        my_total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE seller_id=$1", message.from_user.id)
except Exception:
    logger.exception("Stats DB error")
    await message.answer("⚠️ Statistikani olishda xatolik yuz berdi.")
    return

text = (
    f"📊 Savdo statistikasi:\n\n"
    f"💰 Umumiy daromad: {total}\n"
    f"📅 Bugungi: {today}\n"
    f"🗓 Oylik: {month}\n"
    f"📆 Yillik: {year}\n\n"
    f"👤 Sizning umumiy savdolaringiz: {my_total}"
)
await message.answer(text)

# Grafik yuborish (katta yuklamaslik uchun oddiy bar)
try:
    labels = ["Bugungi", "Oylik", "Yillik"]
    values = [float(today), float(month), float(year)]
    plt.figure(figsize=(6,4))
    bars = plt.bar(labels, values)
    for bar in bars:
        h = bar.get_height()
        plt.text(bar.get_x() + bar.get_width()/2, h, f"{h:.2f}", ha="center", va="bottom")
    plt.title("Savdo statistikasi")
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    await message.answer_photo(photo=FSInputFile(buf, filename="stats.png"), caption="Grafik")
except Exception:
    logger.exception("Chart error (ignored)")
```

# --- Run ---

async def main():
global db_pool
db_pool = await init_db_pool()
logger.info("Bot ishga tushmoqda...")
await dp.start_polling(bot)

if **name** == "**main**":
try:
asyncio.run(main())
except (KeyboardInterrupt, SystemExit):
logger.info("Bot to'xtatildi")
