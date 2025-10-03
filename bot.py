import logging
import io
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
from fpdf import FPDF
import asyncio

API_TOKEN = os.getenv('BOT_TOKEN')  # Берём из переменных окружения
ADMIN_ID = int(os.getenv('ADMIN_ID'))  # Твой Telegram ID (число)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Базы данных в памяти
products = {}
product_id_seq = 1

clients = {}
sales = {}
sale_id_seq = 1

# Проверка, админ ли пользователь
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


@dp.message(Command("start"))
async def start(message: types.Message):
    clients[message.from_user.id] = message.from_user.username or message.from_user.full_name
    await message.answer(
        "Привет! Я CRM бот.\n"
        "Если ты админ — используй /help_admin\n"
        "Если клиент — просто жди уведомлений."
    )


@dp.message(Command("help_admin"))
async def help_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    text = (
        "Команды для админа:\n"
        "/add_product - Добавить товар\n"
        "/list_products - Показать товары\n"
        "/sell - Оформить продажу"
    )
    await message.answer(text)


@dp.message(Command("add_product"))
async def add_product(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    await message.answer("Введите название и цену товара через запятую, например:\nПылесос, 4500")
    # Регистрация следующего хендлера для ввода товара
    dp.message.register(handle_add_product, lambda msg: is_admin(msg.from_user.id))


async def handle_add_product(message: types.Message):
    global product_id_seq
    text = message.text.strip()
    if ',' not in text:
        await message.answer("Неверный формат. Введите в формате: Название, цена")
        return
    name, price_str = map(str.strip, text.split(',', maxsplit=1))
    try:
        price = float(price_str)
    except ValueError:
        await message.answer("Цена должна быть числом. Попробуйте ещё раз.")
        return

    products[product_id_seq] = {'name': name, 'price': price}
    await message.answer(f"Товар добавлен: {name} — {price} ₽ (id {product_id_seq})")
    product_id_seq += 1
    # Отменяем регистрацию этого хендлера после добавления
    dp.message.unregister(handle_add_product)


@dp.message(Command("list_products"))
async def list_products(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    if not products:
        await message.answer("Товары не добавлены.")
        return
    text = "Список товаров:\n"
    for pid, prod in products.items():
        text += f"{pid}: {prod['name']} — {prod['price']} ₽\n"
    await message.answer(text)


@dp.message(Command("sell"))
async def sell(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Команда доступна только администратору.")
        return
    if not products:
        await message.answer("Сначала добавьте товары командой /add_product")
        return
    await message.answer("Введите Telegram user_id клиента (число)")
    dp.message.register(handle_sell_client, lambda msg: is_admin(msg.from_user.id))


async def handle_sell_client(message: types.Message):
    try:
        client_id = int(message.text.strip())
    except ValueError:
        await message.answer("Некорректный ID клиента, введите число.")
        return

    if client_id not in clients:
        await message.answer("Клиент с таким ID не найден.")
        return

    await message.answer(
        f"Клиент найден: {clients[client_id]}\n"
        "Отправьте через запятую id товаров для продажи. Например: 1,2"
    )
    # Передаём client_id в следующий обработчик через лямбду
    dp.message.register(lambda m: handle_sell_products(m, client_id), lambda msg: is_admin(msg.from_user.id))
    dp.message.unregister(handle_sell_client)


async def handle_sell_products(message: types.Message, client_id: int):
    product_ids_str = message.text.strip().split(',')
    selected_products = []

    for pid_str in product_ids_str:
        try:
            pid = int(pid_str.strip())
        except ValueError:
            await message.answer(f"Некорректный ID товара: {pid_str}")
            return
        if pid not in products:
            await message.answer(f"Товара с ID {pid} нет.")
            return
        selected_products.append(products[pid])

    global sale_id_seq
    sales[sale_id_seq] = {'client_id': client_id, 'products': selected_products}
    sale_id = sale_id_seq
    sale_id_seq += 1

    await message.answer(f"Продажа оформлена, создаём чек для клиента {clients[client_id]}...")

    pdf_bytes = generate_pdf_check(clients[client_id], selected_products)

    pdf_file = FSInputFile(io.BytesIO(pdf_bytes), filename="check.pdf")
    try:
        await bot.send_document(client_id, pdf_file)
    except Exception as e:
        await message.answer(f"Ошибка при отправке чека клиенту: {e}")
        return

    await message.answer("Чек отправлен клиенту.")
    dp.message.unregister(lambda m: handle_sell_products(m, client_id))


def generate_pdf_check(client_name: str, products_list: list) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(0, 10, f"Чек для клиента: {client_name}", ln=1)
    pdf.ln(5)

    total = 0
    for product in products_list:
        line = f"{product['name']} — {product['price']} ₽"
        pdf.cell(0, 10, line, ln=1)
        total += product['price']

    pdf.ln(5)
    pdf.cell(0, 10, f"Итого: {total} ₽", ln=1)

    pdf_output = io.BytesIO()
    pdf.output(pdf_output)
    pdf_output.seek(0)
    return pdf_output.read()


async def main():
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
