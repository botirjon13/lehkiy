#!/usr/bin/env python3
# bot.py

import os
import asyncio
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # Загружаем переменные из .env

import asyncpg
from aiogram.filters import Text
from aiogram.filters.state import StateFilter
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup

# Проверяем переменные окружения
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
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="🛒 Продать товар")],
        [KeyboardButton(text="📊 Статистика")],
    ], resize_keyboard=True)
    return kb

def payment_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💵 Наличные"), KeyboardButton(text="💳 Карта")],
        [KeyboardButton(text="📅 В долг")],
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

# --- Добавление товара ---
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
    except Exception:
        await message.answer("Ошибка в формате данных. Попробуй ещё раз.")
        return

    async with db_pool.acquire() as conn:
        product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
        if product:
            await conn.execute(
                "UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3",
                qty, price, product['id']
            )
            await message.answer(f"Обновлён товар: {name} — добавлено {qty} шт., цена обновлена до {price}")
        else:
            await conn.execute(
                "INSERT INTO products(name, quantity, price) VALUES($1, $2, $3)",
                name, qty, price
            )
            await message.answer(f"Добавлен новый товар: {name}, количество: {qty}, цена: {price}")
    await state.clear()

# --- Статистика ---
@dp.message(Text("📊 Статистика"))
async def show_stats(message: types.Message):
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COALESCE(SUM(total), 0) FROM sales")
    await message.answer(f"Общий доход: {total} у.е.")

# --- Продажа товара ---
@dp.message(Text("🛒 Продать товар"))
async def start_sell(message: types.Message, state: FSMContext):
    await state.set_state(SellStates.waiting_for_product)
    async with db_pool.acquire() as conn:
        products = await conn.fetch("SELECT name FROM products")
    if not products:
        await message.answer("Нет товаров для продажи. Сначала добавьте товар.")
        await state.clear()
        return
    # Здесь не отправляем список, пользователь вводит текст для автодополнения
    await message.answer("Введите название товара для продажи (начните вводить, и я подскажу)")

# --- Хендлер автодополнения товаров ---
@dp.message(StateFilter(SellStates.waiting_for_product))
async def process_product_autocomplete(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if len(text) < 2:
        await message.answer("Введите минимум 2 символа для поиска товара.")
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name FROM products WHERE LOWER(name) LIKE LOWER($1) ORDER BY name LIMIT 5",
            f"%{text}%"
        )
    if not rows:
        await message.answer("Товары не найдены. Попробуйте другое название.")
        return
    buttons = [
        InlineKeyboardButton(text=record['name'], callback_data=f"select_product:{record['name']}")
        for record in rows
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn] for btn in buttons])
    await message.answer("Выберите товар из списка:", reply_markup=kb)

# --- Обработка выбора товара из подсказок ---
@dp.callback_query(Text(startswith="select_product:"))
async def select_product_callback(call: types.CallbackQuery, state: FSMContext):
    product_name = call.data.split("select_product:")[1]
    async with db_pool.acquire() as conn:
        product = await conn.fetchrow("SELECT * FROM products WHERE name = $1", product_name)
    if not product:
        await call.answer("Товар не найден.", show_alert=True)
        return
    await state.update_data(product=product)
    await state.set_state(SellStates.waiting_for_quantity)
    await bot.send_message(
        call.from_user.id,
        f"Вы выбрали: {product_name}\nВведите количество для продажи (в наличии {product['quantity']})"
    )
    await call.answer()

@dp.message(SellStates.waiting_for_quantity)
async def process_sell_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    product = data['product']
    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("Введите корректное положительное число для количества.")
        return
    if qty > product['quantity']:
        await message.answer(f"Недостаточно товара. В наличии {product['quantity']} шт.")
        return
    await state.update_data(quantity=qty)
    await state.set_state(SellStates.waiting_for_client_name)
    await message.answer("Введите имя клиента")

@dp.message(SellStates.waiting_for_client_name)
async def process_client_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Имя клиента не может быть пустым. Попробуйте ещё раз.")
        return
    await state.update_data(client_name=name)
    await state.set_state(SellStates.waiting_for_client_phone)
    await message.answer("Введите телефон клиента")

@dp.message(SellStates.waiting_for_client_phone)
async def process_client_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if not phone:
        await message.answer("Телефон не может быть пустым. Попробуйте ещё раз.")
        return
    await state.update_data(client_phone=phone)
    await state.set_state(SellStates.waiting_for_payment)
    await message.answer("Выберите способ оплаты", reply_markup=payment_kb())

@dp.message(SellStates.waiting_for_payment)
async def process_payment(message: types.Message, state: FSMContext):
    pay_method = message.text.strip()
    if pay_method not in ["💵 Наличные", "💳 Карта", "📅 В долг"]:
        await message.answer("Выберите способ оплаты с клавиатуры.")
        return
    await state.update_data(payment_method=pay_method)
    data = await state.get_data()
    product = data['product']
    qty = data['quantity']
    total = Decimal(product['price']) * qty
    await message.answer(
        f"Подтвердите продажу:\n\n"
        f"Товар: {product['name']}\n"
        f"Количество: {qty}\n"
        f"Цена за единицу: {product['price']}\n"
        f"Общая сумма: {total}\n"
        f"Клиент: {data['client_name']}\n"
        f"Телефон: {data['client_phone']}\n"
        f"Оплата: {pay_method}\n\n"
        f"Напишите 'да' для подтверждения или 'отмена' для отмены."
    )
    await state.set_state(SellStates.confirm)

@dp.message(SellStates.confirm)
async def process_confirm(message: types.Message, state: FSMContext):
    text = message.text.strip().lower()
    if text == "да":
        data = await state.get_data()
        product = data['product']
        qty = data['quantity']
        client_name = data['client_name']
        client_phone = data['client_phone']
        payment_method = data['payment_method']
        total = Decimal(product['price']) * qty
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO sales(product_id, quantity, price, total, client_name, client_phone, payment_method, sale_date) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                product['id'], qty, product['price'], total,
                client_name, client_phone, payment_method, datetime.now()
            )
            await conn.execute(
                "UPDATE products SET quantity=quantity-$1 WHERE id=$2",
                qty, product['id']
            )
        await message.answer("Продажа успешно оформлена!", reply_markup=main_menu_kb())
        await state.clear()
    elif text == "отмена":
        await message.answer("Операция отменена.", reply_markup=main_menu_kb())
        await state.clear()
    else:
        await message.answer("Напишите 'да' для подтверждения или 'отмена' для отмены.")

# --- Запуск бота ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
