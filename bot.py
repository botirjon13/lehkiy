import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import asyncpg
import asyncio

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "7196045219:AAFfbeIZQXKAb_cgAC2cnbdMY__L0Iakcrg"
ADMIN_ID = 1262207928  # ваш ID для уведомлений

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DATABASE_URL = "postgresql://postgres:CHLLglOdBiZEuGZUcfyhYwfTDoxhklIe@yamanote.proxy.rlwy.net:53203/railway"

db_pool = None

# --- Клавиатуры ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить товар")],
        [KeyboardButton(text="Продать товар")],
        [KeyboardButton(text="Статистика")]
    ],
    resize_keyboard=True
)

payment_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Наличные", callback_data="pay_cash"),
            InlineKeyboardButton(text="Клик на карту", callback_data="pay_click"),
            InlineKeyboardButton(text="В долг", callback_data="pay_credit"),
        ]
    ],
    row_width=3
)

# --- Временные данные ---
user_states = {}  # для пошагового ввода
user_cart = {}    # корзина продаж

# --- Функции работы с базой ---
async def create_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

async def add_product(name, quantity, price):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO products (name, quantity, price) VALUES ($1, $2, $3)",
            name, quantity, price
        )

async def get_products():
    async with db_pool.acquire() as conn:
        return await conn.fetch("SELECT id, name, quantity, price FROM products")

async def update_product_quantity(product_id, quantity_change):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE products SET quantity = quantity + $1 WHERE id = $2",
            quantity_change, product_id
        )

async def add_client(name, phone):
    async with db_pool.acquire() as conn:
        # попробуем вставить, если уже есть — вернуть id
        client = await conn.fetchrow("SELECT id FROM clients WHERE phone = $1", phone)
        if client:
            return client["id"]
        else:
            row = await conn.fetchrow(
                "INSERT INTO clients (name, phone) VALUES ($1, $2) RETURNING id",
                name, phone
            )
            return row["id"]

async def add_sale(client_id, payment_method, total, items):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            sale = await conn.fetchrow(
                "INSERT INTO sales (client_id, payment_method, total) VALUES ($1, $2, $3) RETURNING id",
                client_id, payment_method, total
            )
            sale_id = sale["id"]
            for item in items:
                await conn.execute(
                    "INSERT INTO sale_items (sale_id, product_id, quantity, price) VALUES ($1, $2, $3, $4)",
                    sale_id, item['product_id'], item['quantity'], item['price']
                )
                # уменьшаем количество товара на складе
                await update_product_quantity(item['product_id'], -item['quantity'])
            return sale_id

# --- Хендлеры ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Это CRM-бот.", reply_markup=main_kb)

# Добавление товара — пошаговый ввод
@dp.message(Text("Добавить товар"))
async def add_product_start(message: types.Message):
    user_states[message.from_user.id] = {"step": 1, "data": {}}
    await message.answer("Введите название товара:")

@dp.message()
async def process_add_product(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return
    state = user_states[user_id]

    if state["step"] == 1:
        state["data"]["name"] = message.text.strip()
        state["step"] = 2
        await message.answer("Введите количество:")
    elif state["step"] == 2:
        if not message.text.isdigit():
            await message.answer("Количество должно быть числом. Введите количество:")
            return
        state["data"]["quantity"] = int(message.text)
        state["step"] = 3
        await message.answer("Введите цену за единицу (например 5000):")
    elif state["step"] == 3:
        try:
            price = float(message.text.replace(",", "."))
        except ValueError:
            await message.answer("Цена должна быть числом. Введите цену:")
            return
        state["data"]["price"] = price

        # Добавляем товар в БД
        await add_product(state["data"]["name"], state["data"]["quantity"], state["data"]["price"])
        await message.answer(f"Товар {state['data']['name']} добавлен в склад.")
        user_states.pop(user_id)

# Продажа товара — показываем товары для выбора
@dp.message(Text("Продать товар"))
async def sell_product_start(message: types.Message):
    products = await get_products()
    if not products:
        await message.answer("Склад пуст. Добавьте товары.")
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for p in products:
        kb.add(InlineKeyboardButton(text=f"{p['name']} (в наличии {p['quantity']})", callback_data=f"sell_{p['id']}"))

    user_cart[message.from_user.id] = []  # новая корзина
    await message.answer("Выберите товар для продажи:", reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("sell_"))
async def process_sell_product(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    product_id = int(callback.data.split("_")[1])

    # Сохраняем выбранный товар в состоянии для ввода количества и цены
    user_states[user_id] = {"step": "sell_quantity", "product_id": product_id}
    await callback.message.answer("Введите количество товара для продажи:")
    await callback.answer()

@dp.message()
async def process_sell_quantity_price(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_states:
        return
    state = user_states[user_id]

    if state.get("step") == "sell_quantity":
        if not message.text.isdigit():
            await message.answer("Количество должно быть числом. Введите количество:")
            return
        state["quantity"] = int(message.text)
        state["step"] = "sell_price"
        await message.answer("Введите цену продажи за единицу:")
    elif state.get("step") == "sell_price":
        try:
            price = float(message.text.replace(",", "."))
        except ValueError:
            await message.answer("Цена должна быть числом. Введите цену:")
            return
        state["price"] = price
        state["step"] = "confirm_add_to_cart"

        # Добавляем товар в корзину
        if user_id not in user_cart:
            user_cart[user_id] = []
        user_cart[user_id].append({
            "product_id": state["product_id"],
            "quantity": state["quantity"],
            "price": state["price"]
        })
        await message.answer(f"Товар добавлен в корзину. Для добавления других товаров нажмите 'Продать товар', для оформления нажмите /checkout")
        user_states.pop(user_id)

# Команда /checkout — подтверждение и выбор оплаты
@dp.message(Command("checkout"))
async def checkout(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_cart or not user_cart[user_id]:
        await message.answer("Корзина пуста.")
        return

    # Подсчёт суммы
    total = sum(item['quantity'] * item['price'] for item in user_cart[user_id])
    user_states[user_id] = {"step": "payment_selection", "total": total}
    await message.answer(f"Итоговая сумма: {total}.\nВыберите способ оплаты:", reply_markup=payment_kb)

@dp.callback_query(Text(startswith="pay_"))
async def process_payment(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_states or user_states[user_id].get("step") != "payment_selection":
        await callback.answer("Пожалуйста, начните оформление через /checkout")
        return

    payment_method = callback.data[4:]
    state = user_states[user_id]

    # Для простоты спросим имя и телефон клиента
    user_states[user_id] = {"step": "client_info", "payment_method": payment_method, "total": state["total"]}

    await callback.message.answer("Введите имя клиента:")
    await callback.answer()

@dp.message()
async def process_client_info(message: types.Message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id].get("step") != "client_info":
        return
    state = user_states[user_id]

    if "name" not in state:
        state["name"] = message.text.strip()
        await message.answer("Введите номер телефона клиента:")
    else:
        phone = message.text.strip()
        state["phone"] = phone

        # Добавляем клиента в БД
        client_id = await add_client(state["name"], state["phone"])

        # Добавляем продажу
        sale_id = await add_sale(client_id, state["payment_method"], state["total"], user_cart[user_id])

        # Формируем чек
        receipt = f"Чек продажи №{sale_id}\nДата: сейчас\nКлиент: {state['name']}\nТелефон: {state['phone']}\n\nТовары:\n"
        for item in user_cart[user_id]:
            # Для простоты можно получить название из БД, но сейчас просто id
            receipt += f"- Товар ID {item['product_id']}, Кол-во: {item['quantity']}, Цена: {item['price']}\n"
        receipt += f"\nИтого: {state['total']}\nОплата: {state['payment_method']}"

        await message.answer(receipt)
        if ADMIN_ID:
            await bot.send_message(ADMIN_ID, f"Новая продажа:\n{receipt}")

        # Очистка состояний и корзины
        user_states.pop(user_id)
        user_cart.pop(user_id)

# --- Статистика ---
@dp.message(Text("Статистика"))
async def stats(message: types.Message):
    async with db_pool.acquire() as conn:
        # Подсчёт дохода за день, месяц, год
        day = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE sale_date::date = CURRENT_DATE")
        month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE date_trunc('month', sale_date) = date_trunc('month', CURRENT_DATE)")
        year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE date_trunc('year', sale_date) = date_trunc('year', CURRENT_DATE)")

    await message.answer(f"Доходы:\nСегодня: {day}\nЭтот месяц: {month}\nЭтот год: {year}")

# --- Запуск ---
async def on_startup():
    await create_pool()
    logging.info("Бот запущен!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(on_startup())
    from aiogram import executor
    executor.start_polling(dp, skip_updates=True)
