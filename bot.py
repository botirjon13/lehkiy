import logging
from aiogram import Bot, Dispatcher, executor, types
from fpdf import FPDF
import io

API_TOKEN = os.getenv('API_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID'))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Простая база товаров: {id: {'name': str, 'price': float}}
products = {}
product_id_seq = 1

# Простая база клиентов: {user_id: username}
clients = {}

# Продажи: {sale_id: {'client_id': int, 'products': [product_ids]}}
sales = {}
sale_id_seq = 1


def is_admin(user_id):
    return user_id == ADMIN_ID


@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    clients[message.from_user.id] = message.from_user.username or message.from_user.full_name
    await message.reply("Привет! Я CRM бот.\nЕсли ты админ — используй /help_admin\nЕсли клиент — жди уведомлений.")


@dp.message_handler(commands=['help_admin'])
async def help_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("Команда только для админа.")
        return
    text = (
        "Команды для админа:\n"
        "/add_product - Добавить товар\n"
        "/list_products - Показать товары\n"
        "/sell - Оформить продажу"
    )
    await message.reply(text)


@dp.message_handler(commands=['add_product'])
async def add_product(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("Команда только для админа.")
        return
    await message.reply("Введите название и цену товара через запятую, например:\n"
                        "Пылесос, 4500")


@dp.message_handler()
async def handle_text(message: types.Message):
    if is_admin(message.from_user.id):
        text = message.text.strip()
        global product_id_seq

        # Проверяем, добавляем ли товар (формат: название, цена)
        if ',' in text:
            parts = text.split(',', maxsplit=1)
            name = parts[0].strip()
            try:
                price = float(parts[1].strip())
            except ValueError:
                await message.reply("Неверный формат цены. Попробуйте ещё раз.")
                return
            products[product_id_seq] = {'name': name, 'price': price}
            await message.reply(f"Товар добавлен: {name} за {price} ₽ (id {product_id_seq})")
            product_id_seq += 1
            return

        # Продажа - по команде /sell бот предложит дальнейшие шаги
        if text.startswith('/sell'):
            if not products:
                await message.reply("Сначала добавьте товары командой /add_product")
                return
            await message.reply("Отправьте id клиента (число Telegram user_id)")
            dp.register_message_handler(handle_sell_client, state=None)
            return

    else:
        await message.reply("Я не понимаю. Если вы клиент, ждите уведомлений.")


async def handle_sell_client(message: types.Message):
    client_id = None
    try:
        client_id = int(message.text.strip())
    except ValueError:
        await message.reply("Некорректный ID клиента, введите число.")
        return

    if client_id not in clients:
        await message.reply("Клиент с таким ID не найден.")
        return

    await message.reply("Клиент найден: " + clients[client_id] + "\n"
                        "Отправьте через запятую id товаров для продажи. Например: 1,2")
    dp.register_message_handler(lambda m: handle_sell_products(m, client_id), state=None)
    dp.message_handlers.unregister(handle_sell_client)


async def handle_sell_products(message: types.Message, client_id):
    product_ids_str = message.text.strip().split(',')
    selected_products = []

    for pid_str in product_ids_str:
        try:
            pid = int(pid_str.strip())
        except ValueError:
            await message.reply(f"Некорректный ID товара: {pid_str}")
            return
        if pid not in products:
            await message.reply(f"Товара с ID {pid} нет.")
            return
        selected_products.append(products[pid])

    global sale_id_seq
    sales[sale_id_seq] = {'client_id': client_id, 'products': selected_products}
    sale_id = sale_id_seq
    sale_id_seq += 1

    await message.reply(f"Продажа оформлена, создаём чек для клиента {clients[client_id]}...")

    # Генерируем PDF чек
    pdf_bytes = generate_pdf_check(clients[client_id], selected_products)

    await bot.send_document(client_id, ('check.pdf', pdf_bytes))
    await message.reply("Чек отправлен клиенту.")
    dp.message_handlers.unregister(handle_sell_products)


def generate_pdf_check(client_name, products_list):
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


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
