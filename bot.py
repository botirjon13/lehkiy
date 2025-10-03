import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
import asyncpg
from datetime import datetime

API_TOKEN = "7196045219:AAFfbeIZQXKAb_cgAC2cnbdMY__L0Iakcrg"
DATABASE_URL = "postgresql://postgres:CHLLglOdBiZEuGZUcfyhYwfTDoxhklIe@yamanote.proxy.rlwy.net:53203/railway"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Подключение к БД
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

db_pool = None

# Инициализация таблиц, если нужно
async def init_db():
    async with db_pool.acquire() as conn:
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

# Клавиатуры
main_kb = InlineKeyboardMarkup(row_width=2).add(
    InlineKeyboardButton("➕ Добавить товар", callback_data="add_product"),
    InlineKeyboardButton("🛒 Продать товар", callback_data="sell_product"),
    InlineKeyboardButton("📊 Статистика", callback_data="stats")
)

payment_kb = InlineKeyboardMarkup(row_width=3).add(
    InlineKeyboardButton("💵 Наличными", callback_data="pay_cash"),
    InlineKeyboardButton("💳 Картой", callback_data="pay_card"),
    InlineKeyboardButton("📅 В долг", callback_data="pay_debt"),
)

# Хранилище временных данных продаж по пользователям (в идеале FSM)
users_cart = {}

@dp.message(commands=["start"])
async def cmd_start(message: Message):
    await message.answer("Привет! Это CRM бот.\nВыбери действие:", reply_markup=main_kb)

@dp.callback_query()
async def callbacks_handler(query: CallbackQuery):
    user_id = query.from_user.id
    data = query.data

    if data == "add_product":
        await query.message.answer("Введите товар в формате:\nНазвание, количество, цена\nНапример:\nМолоко, 10, 5000")
        await query.answer()

        # Помечаем, что следующий текст от этого пользователя - добавление товара
        users_cart[user_id] = {"state": "adding_product"}

    elif data == "sell_product":
        # Показываем список товаров для выбора
        async with db_pool.acquire() as conn:
            products = await conn.fetch("SELECT id, name, quantity, price FROM products WHERE quantity > 0")
        if not products:
            await query.message.answer("Нет товаров на складе для продажи.")
            await query.answer()
            return

        kb = InlineKeyboardMarkup(row_width=1)
        for p in products:
            kb.insert(InlineKeyboardButton(f"{p['name']} (в наличии: {p['quantity']}, цена: {p['price']})", callback_data=f"sell_{p['id']}"))
        await query.message.answer("Выберите товар для продажи:", reply_markup=kb)
        await query.answer()

    elif data.startswith("sell_"):
        product_id = int(data.split("_")[1])
        users_cart[user_id] = {"state": "selling_product", "product_id": product_id}
        await query.message.answer("Введите количество для продажи:")
        await query.answer()

    elif data in ("pay_cash", "pay_card", "pay_debt"):
        if user_id not in users_cart or users_cart[user_id].get("state") != "waiting_payment":
            await query.answer("Нет текущей продажи.")
            return

        payment_method = {"pay_cash": "Наличные", "pay_card": "Карта", "pay_debt": "В долг"}[data]
        sale_info = users_cart[user_id]

        # Сохраняем продажу в БД
        async with db_pool.acquire() as conn:
            product = await conn.fetchrow("SELECT * FROM products WHERE id=$1", sale_info["product_id"])

            total = sale_info["quantity"] * sale_info["price"]

            await conn.execute("""
                INSERT INTO sales(product_id, quantity, price, total, client_name, client_phone, payment_method, sale_date)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """, sale_info["product_id"], sale_info["quantity"], sale_info["price"], total,
                 sale_info.get("client_name", "Не указано"),
                 sale_info.get("client_phone", "Не указано"),
                 payment_method,
                 datetime.now())

            # Обновляем количество товара на складе
            new_qty = product["quantity"] - sale_info["quantity"]
            await conn.execute("UPDATE products SET quantity=$1 WHERE id=$2", new_qty, sale_info["product_id"])

        # Формируем чек
        receipt = f"""🧾 Чек
Дата: {datetime.now().strftime("%d-%m-%Y %H:%M")}

Товар: {product['name']}
Количество: {sale_info['quantity']}
Цена за штуку: {sale_info['price']}
Итог: {total}

Клиент: {sale_info.get("client_name", "Не указано")}
Телефон: {sale_info.get("client_phone", "Не указано")}
Способ оплаты: {payment_method}
"""

        await query.message.answer(receipt)
        await query.answer("Продажа оформлена!")

        # Очистить состояние
        users_cart.pop(user_id, None)

    elif data == "stats":
        async with db_pool.acquire() as conn:
            result = await conn.fetchrow("SELECT SUM(total) as total_income FROM sales")
        total_income = result["total_income"] or 0
        await query.message.answer(f"Общий доход: {total_income} сум")
        await query.answer()

@dp.message()
async def message_handler(message: Message):
    user_id = message.from_user.id
    if user_id not in users_cart:
        await message.answer("Выберите действие через кнопки", reply_markup=main_kb)
        return

    state = users_cart[user_id].get("state")

    if state == "adding_product":
        try:
            name, qty, price = [x.strip() for x in message.text.split(",")]
            qty = int(qty)
            price = float(price)
        except Exception:
            await message.answer("Неверный формат. Попробуйте еще раз:\nНазвание, количество, цена")
            return

        async with db_pool.acquire() as conn:
            # Вставляем или обновляем товар
            existing = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
            if existing:
                await conn.execute("UPDATE products SET quantity=quantity+$1, price=$2 WHERE id=$3", qty, price, existing["id"])
            else:
                await conn.execute("INSERT INTO products(name, quantity, price) VALUES ($1,$2,$3)", name, qty, price)

        users_cart.pop(user_id)
        await message.answer(f"Товар '{name}' добавлен/обновлен успешно.", reply_markup=main_kb)

    elif state == "selling_product":
        try:
            quantity = int(message.text)
        except ValueError:
            await message.answer("Введите корректное число для количества.")
            return

        users_cart[user_id]["quantity"] = quantity
        users_cart[user_id]["state"] = "enter_price"
        await message.answer("Введите цену за штуку:")

    elif state == "enter_price":
        try:
            price = float(message.text)
        except ValueError:
            await message.answer("Введите корректную цену.")
            return

        users_cart[user_id]["price"] = price
        users_cart[user_id]["state"] = "enter_client_name"
        await message.answer("Введите имя клиента:")

    elif state == "enter_client_name":
        users_cart[user_id]["client_name"] = message.text.strip()
        users_cart[user_id]["state"] = "enter_client_phone"
        await message.answer("Введите номер телефона клиента:")

    elif state == "enter_client_phone":
        users_cart[user_id]["client_phone"] = message.text.strip()
        users_cart[user_id]["state"] = "waiting_payment"
        await message.answer("Выберите способ оплаты:", reply_markup=payment_kb)

    else:
        await message.answer("Выберите действие через кнопки", reply_markup=main_kb)


async def main():
    global db_pool
    db_pool = await create_db_pool()
    await init_db()
    print("База данных и бот готовы!")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
