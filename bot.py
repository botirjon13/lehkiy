import os
import asyncio
import io
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile, ContentType
)
from fpdf import FPDF
from db import connect_db, create_tables  # Предполагаемая ваша БД-логика
from aiogram.types import ContentType

API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Клавиатуры
request_contact_btn = KeyboardButton(text="Рақамимни юбориш", request_contact=True)
register_kb = ReplyKeyboardMarkup(keyboard=[[request_contact_btn]], resize_keyboard=True)

admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Маҳсулот қўшиш")],
        [KeyboardButton(text="Маҳсулотлар рўйхати")],
        [KeyboardButton(text="Сотувни амалга ошириш")],
        [KeyboardButton(text="Ҳисоботлар")]
    ],
    resize_keyboard=True
)

client_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Менинг буюртмаларим")]],
    resize_keyboard=True
)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# Регистрация клиента с подтверждением контакта
@dp.message(Command("start"))
async def start_handler(message: types.Message):
    conn = await connect_db()
    client = await conn.fetchrow("SELECT id FROM clients WHERE id=$1", message.from_user.id)
    if client:
        if is_admin(message.from_user.id):
            await message.answer("Хуш келибсиз, админ!", reply_markup=admin_kb)
        else:
            await message.answer("Ассалому алайкум! Рақамингизни юборишингиз лозим.", reply_markup=register_kb)
    else:
        await message.answer("Ассалому алайкум! Илтимос, рақамингизни юборинг.", reply_markup=register_kb)
    await conn.close()

@dp.message(content_types=ContentType.CONTACT)
async def contact_handler(message: types.Message):
    if message.contact and message.contact.user_id == message.from_user.id:
        conn = await connect_db()
        await conn.execute(
            "INSERT INTO clients (id, username, phone) VALUES ($1, $2, $3) ON CONFLICT (id) DO UPDATE SET phone = EXCLUDED.phone",
            message.from_user.id,
            message.from_user.username,
            message.contact.phone_number
        )
        await conn.close()
        await message.answer("Рақамингиз муваффақиятли сақланди! Энди сиз маҳсулотлардан фойдаланишингиз мумкин.", reply_markup=client_kb)
    else:
        await message.answer("Илтимос, ўз рақамингизни юборинг.")

# Пример добавления товара (для админа)
@dp.message(Text(text="Маҳсулот қўшиш"))
async def add_product(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Фақат админга рухсат берилади.")
        return
    await message.answer("Илтимос, маҳсулот номини ва нархини қуйидаги форматда юборинг:\nНоми;Нарх\nМисол: Мазали нон;12000")

@dp.message()
async def process_product_info(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    if ";" in message.text:
        try:
            name, price = message.text.split(";")
            price = float(price)
            conn = await connect_db()
            await conn.execute(
                "INSERT INTO products (name, price) VALUES ($1, $2)",
                name.strip(), price
            )
            await conn.close()
            await message.answer(f"Маҳсулот '{name.strip()}' нархи {price} сўм билан қўшилди.")
        except Exception as e:
            await message.answer("Маълумот нотўғри форматда ёзилган ёки бошқа хатолик. Қайта уриниб кўринг.")
    else:
        pass  # Можно добавить другие обработчики

# Оформление продажи (админ)
@dp.message(Text(text="Сотувни амалга ошириш"))
async def start_sale(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Фақат админга рухсат берилади.")
        return
    await message.answer("Илтимос, сотув учун мижознинг Telegram ID ни киритинг:")

    # Запускаем состояние FSM, чтобы далее обрабатывать ID клиента, товары и т.п.
    # Здесь для упрощения сделаем простой запрос в несколько шагов (можно расширить с FSM)

# Обработка фото чека от админа или клиента
@dp.message(content_types=ContentType.PHOTO)
async def handle_check_photo(message: types.Message):
    # Сохраняем фото
    file_id = message.photo[-1].file_id  # Самое большое фото
    file = await bot.get_file(file_id)
    file_path = file.file_path
    saved_path = f"./checks/{file_id}.jpg"

    # Создаем папку, если нет
    os.makedirs("checks", exist_ok=True)

    await bot.download_file(file_path, saved_path)

    await message.answer("Чек расми сақланди.")

    # Отправим фото клиенту и админу (если нужно)
    await bot.send_photo(ADMIN_ID, photo=file_id, caption="Янги чек расми қабул қилинди.")
    await message.answer_photo(photo=file_id, caption="Сизнинг чек расмингиз қабул қилинди.")

# Генерация отчётов (простой пример)
@dp.message(Text(text="Ҳисоботлар"))
async def reports(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Фақат админга рухсат берилади.")
        return

    conn = await connect_db()
    total_sales = await conn.fetchval("SELECT SUM(total_amount) FROM sales")
    sales_count = await conn.fetchval("SELECT COUNT(*) FROM sales")

    await message.answer(f"Жами сотувлар сони: {sales_count}\nЖами даромад: {total_sales} сўм")

    await conn.close()

# Пример генерации PDF чека
def generate_pdf_check(client_name: str, products_list: list, date: datetime) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(0, 10, f"Харидор: {client_name}", ln=1)
    pdf.cell(0, 10, f"Сана: {date.strftime('%Y-%m-%d %H:%M')}", ln=1)
    pdf.ln(5)

    total = 0
    for product in products_list:
        name = product['name']
        qty = product.get('quantity', 1)
        price = product['price']
        pdf.cell(0, 10, f"{name} — {qty} × {price} сўм", ln=1)
        total += price * qty

    pdf.ln(5)
    pdf.cell(0, 10, f"Жами: {total} сўм", ln=1)

    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return pdf_output.read()

async def main():
    conn = await connect_db()
    await create_tables(conn)
    await conn.close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
