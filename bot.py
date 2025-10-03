import os
import asyncio
import io
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from fpdf import FPDF
from db import connect_db, create_tables

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Добавить товар")],
        [KeyboardButton(text="Список товаров")],
        [KeyboardButton(text="Оформить продажу")]
    ],
    resize_keyboard=True
)
client_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Мои заказы")]],
    resize_keyboard=True
)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

@dp.message(Command("start"))
async def start(message: types.Message):
    conn = await connect_db()
    await create_tables(conn)

    await conn.execute(
        "INSERT INTO clients (id, username) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        message.from_user.id, message.from_user.username or ""
    )
    await conn.close()

    if is_admin(message.from_user.id):
        await message.answer("Добро пожаловать, админ!", reply_markup=admin_kb)
    else:
        await message.answer("Привет! Нажми кнопку ниже, чтобы посмотреть свои заказы.", reply_markup=client_kb)

def generate_pdf_check(client_name: str, products_list: list, date: datetime) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(0, 10, f"Чек для клиента: {client_name}", ln=1)
    pdf.cell(0, 10, f"Дата: {date.strftime('%Y-%m-%d %H:%M')}", ln=1)
    pdf.ln(5)
    total = 0
    for p in products_list:
        name = p['name']
        price = p['price']
        pdf.cell(0, 10, f"{name} — {price} ₽", ln=1)
        total += float(price)
    pdf.ln(5)
    pdf.cell(0, 10, f"Итого: {total} ₽", ln=1)

    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)
    return buf.read()

@dp.message()
async def handle_buttons(message: types.Message):
    user_id = message.from_user.id
    text = message.text

    if is_admin(user_id):
        # Админские кнопки
        if text == "Добавить товар":
            await message.answer("Введите название и цену через запятую, например: Товар, 100")
            dp.message.register(handle_add_product, lambda msg: msg.from_user.id == ADMIN_ID)
            return

        if text == "Список товаров":
            conn = await connect_db()
            rows = await conn.fetch("SELECT id, name, price FROM products")
            await conn.close()
            if not rows:
                await message.answer("Список товаров пуст.")
            else:
                s = "\n".join([f"{row['id']}: {row['name']} — {row['price']} ₽" for row in rows])
                await message.answer("Товары:\n" + s)
            return

        if text == "Оформить продажу":
            await message.answer("Введите ID клиента (число):")
            dp.message.register(handle_sell_client, lambda msg: msg.from_user.id == ADMIN_ID)
            return

    # Для клиента
    if text == "Мои заказы":
        await handle_my_orders(message)
        return

async def handle_add_product(message: types.Message):
    text = message.text.strip()
    if "," not in text:
        await message.answer("Неверный формат, используйте: Название, цена")
        return
    name, price_str = map(str.strip, text.split(",", 1))
    try:
        price = float(price_str)
    except:
        await message.answer("Цена должна быть числом")
        return

    conn = await connect_db()
    await conn.execute("INSERT INTO products(name, price) VALUES ($1, $2)", name, price)
    await conn.close()
    await message.answer(f"Товар добавлен: {name} — {price} ₽")
    dp.message.unregister(handle_add_product)

async def handle_sell_client(message: types.Message):
    try:
        client_id = int(message.text.strip())
    except:
        await message.answer("Некорректный ID клиента")
        return
    # Проверяем клиента
    conn = await connect_db()
    client = await conn.fetchrow("SELECT id FROM clients WHERE id = $1", client_id)
    if not client:
        await message.answer("Клиент не найден")
        await conn.close()
        return
    await conn.close()

    await message.answer("Введите через запятую ID товаров, например: 1,2")
    dp.message.register(lambda m: handle_sell_products(m, client_id), lambda msg: msg.from_user.id == ADMIN_ID)
    dp.message.unregister(handle_sell_client)

async def handle_sell_products(message: types.Message, client_id: int):
    parts = message.text.strip().split(",")
    product_ids = []
    for p in parts:
        try:
            pid = int(p.strip())
        except:
            await message.answer(f"Неверный ID товара: {p}")
            return
        product_ids.append(pid)

    conn = await connect_db()
    # Вставляем продажу
    res = await conn.fetchrow("INSERT INTO sales(client_id) VALUES ($1) RETURNING id", client_id)
    sale_id = res['id']
    # Вставляем связи
    for pid in product_ids:
        await conn.execute("INSERT INTO sales_products(sale_id, product_id) VALUES ($1, $2)", sale_id, pid)
    await conn.close()

    await message.answer("Продажа оформлена, чек будет отправлен клиенту.")
    # Отправка чека
    # Получаем данные для чека
    conn2 = await connect_db()
    client = await conn2.fetchrow("SELECT username FROM clients WHERE id = $1", client_id)
    products = await conn2.fetch("""
        SELECT p.name, p.price
        FROM sales_products sp
        JOIN products p ON p.id = sp.product_id
        WHERE sp.sale_id = $1
    """, sale_id)
    await conn2.close()

    pdf = generate_pdf_check(client['username'] or "Клиент", products, datetime.now())
    file = FSInputFile(io.BytesIO(pdf), filename=f"check_{sale_id}.pdf")
    await bot.send_document(client_id, file)

    dp.message.unregister(lambda m: handle_sell_products(m, client_id))

async def handle_my_orders(message: types.Message):
    user_id = message.from_user.id
    conn = await connect_db()
    client = await conn.fetchrow("SELECT username FROM clients WHERE id=$1", user_id)
    if not client:
        await message.answer("Вы не зарегистрированы.")
        await conn.close()
        return

    sales = await conn.fetch("""
        SELECT id, created_at FROM sales
        WHERE client_id = $1
        ORDER BY created_at DESC
    """, user_id)

    if not sales:
        await message.answer("У вас нет заказов.")
        await conn.close()
        return

    for sale in sales:
        products = await conn.fetch("""
            SELECT p.name, p.price
            FROM sales_products sp
            JOIN products p ON p.id = sp.product_id
            WHERE sp.sale_id = $1
        """, sale['id'])
        pdf = generate_pdf_check(client['username'] or "Клиент", products, sale['created_at'])
        file = FSInputFile(io.BytesIO(pdf), filename=f"check_{sale['id']}.pdf")
        await bot.send_document(user_id, file)

    await conn.close()

async def main():
    conn = await connect_db()
    await create_tables(conn)
    await conn.close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
