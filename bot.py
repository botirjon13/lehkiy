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
    await create_tables(conn)  # Автоматически создаёт новые таблицы при старте

    await conn.execute(
        "INSERT INTO clients (id, username) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        message.from_user.id,
        message.from_user.username or ""
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
    for product in products_list:
        name = product['name']
        price = product['price']
        pdf.cell(0, 10, f"{name} — {price} ₽", ln=1)
        total += float(price)

    pdf.ln(5)
    pdf.cell(0, 10, f"Итого: {total} ₽", ln=1)

    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return pdf_output.read()


@dp.message()
async def handle_client_buttons(message: types.Message):
    if message.text == "Мои заказы":
        conn = await connect_db()
        client = await conn.fetchrow("SELECT username FROM clients WHERE id=$1", message.from_user.id)
        if not client:
            await message.answer("Вы не зарегистрированы.")
            return

        sales = await conn.fetch("""
            SELECT id, created_at FROM sales
            WHERE client_id = $1
            ORDER BY created_at DESC
        """, message.from_user.id)

        if not sales:
            await message.answer("У вас нет заказов.")
            return

        for sale in sales:
            products = await conn.fetch("""
                SELECT p.name, p.price
                FROM sales_products sp
                JOIN products p ON p.id = sp.product_id
                WHERE sp.sale_id = $1
            """, sale['id'])

            pdf_bytes = generate_pdf_check(client['username'], products, sale['created_at'])
            file = FSInputFile(io.BytesIO(pdf_bytes), filename=f"check_{sale['id']}.pdf")
            await bot.send_document(message.from_user.id, file)

        await conn.close()


async def main():
    conn = await connect_db()
    await create_tables(conn)
    await conn.close()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main()
