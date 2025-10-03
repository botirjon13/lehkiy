#!/usr/bin/env python3
# bot.py
import os
import asyncio
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Загружает переменные из .env в окружение
#!/usr/bin/env python3
# bot.py

import os
from decimal import Decimal
from datetime import datetime
import asyncio

import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from dotenv import load_dotenv

load_dotenv()  # Загружаем переменные из .env

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN and DATABASE_URL must be set in environment variables")

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

# --- Keyboards ---
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("➕ Добавить товар")],
        [KeyboardButton("🛒 Продать товар")],
        [KeyboardButton("📊 Статистика")],
    ], resize_keyboard=True)
    return kb

def payment_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("💵 Наличные"), KeyboardButton("💳 Карта")],
        [KeyboardButton("📅 В долг")],
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

# --- Handlers ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "Привет! Я простой CRM-бот.\n\n"
        "Доступные команды:\n"
        "➕ Добавить товар — добавить/обновить товар (формат: название,количество,цена) или нажми кнопку\n"
        "🛒 Продать товар — оформить продажу\n"
        "📊 Статистика — показать общий доход\n\n"
        "Пример добавления в одной строке:\n"
        "`Яблоко, 10, 1.50`"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="Markdown")

# Обработка кнопки "➕ Добавить товар"
@dp.message(Text("➕ Добавить товар"))
async def start_add(message: types.Message, state: FSMContext):
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer(
        "Отправь товар в формате: название, количество, цена или просто пришли название и мы спросим дальше.",
        parse_mode="Markdown"
    )

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
            await message.answer(
                "Похоже, формат другой. Введи в формате название, количество, цена (например: Яблоко, 10, 1.50)."
            )
            return
    except Exception as e:
        await message.answer("Ошибка в формате данных. Попробуй ещё раз.")
        return

    async with db_pool.acquire() as conn:
        # Проверяем, есть ли товар с таким именем
        product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if product:
            # Обновляем количество и цену
            await conn.execute(
                "UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3",
                qty, price, product['id']
            )
            await message.answer(f"Обновлён товар: {name} — добавлено {qty} шт., цена обновлена до {price}")
        else:
            # Добавляем новый товар
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1, $2, $3)",
                name, qty, price
            )
            await message.answer(f"Добавлен новый товар: {name}, количество: {qty}, цена: {price}")
    await state.clear()

# Обработка кнопки "📊 Статистика"
@dp.message(Text("📊 Статистика"))
async def show_stats(message: types.Message):
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total), 0) FROM sales")
    await message.answer(f"Общий доход: {total} у.е.")

# Тут можно дальше реализовать остальные состояния и логику продажи товаров

# --- Main entrypoint ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    print("DB pool created, starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
import asyncpg
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # example: postgres://user:pass@host:5432/dbname

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN and DATABASE_URL must be set in environment variables")

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

# global pool will be set in main()
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

# --- Keyboards ---
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("➕ Добавить товар")],
        [KeyboardButton("🛒 Продать товар")],
        [KeyboardButton("📊 Статистика")],
    ], resize_keyboard=True)
    return kb

def payment_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton("💵 Наличные"), KeyboardButton("💳 Карта")],
        [KeyboardButton("📅 В долг")],
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        "Привет! Я простой CRM-бот.\n\n"
        "Доступные команды:\n"
        "➕ Добавить товар — добавить/обновить товар (формат: название,количество,цена) или нажми кнопку\n"
        "🛒 Продать товар — оформить продажу\n"
        "📊 Статистика — показать общий доход\n\n"
        "Пример добавления в одной строке:\n"
        "`Яблоко, 10, 1.50`"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="Markdown")

# Handle main keyboard buttons
@dp.message_handler(lambda message: message.text == "➕ Добавить товар")
async def start_add(message: types.Message, state: FSMContext):
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Отправь товар в формате: название, количество, цена или просто пришли название и мы спросим дальше.", parse_mode="Markdown")

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
            await message.answer("Похоже, формат другой. Введи в формате название, количество, цена (например: Яблоко, 10, 1.50).")
            return
    except Exception as e:
        await message.answer(f"Ошибка при обработке данных: {e}. Попробуйте ещё раз.")
        return

    # Сохраняем или обновляем товар в базе
    async with db_pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM products WHERE name = $1", name)
        if existing:
            await conn.execute("UPDATE products SET quantity = quantity + $1, price = $2 WHERE id = $3", qty, price, existing['id'])
            await message.answer(f"Товар '{name}' обновлён: добавлено {qty} шт., цена обновлена до {price}.")
        else:
            await conn.execute("INSERT INTO products (name, quantity, price) VALUES ($1, $2, $3)", name, qty, price)
            await message.answer(f"Товар '{name}' добавлен: {qty} шт. по цене {price}.")

    await state.clear()

# Далее должны быть остальные обработчики — например, для продажи товара и статистики
# Если нужно, могу помочь с этим тоже

async def main():
    global db_pool
    db_pool = await init_db_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
