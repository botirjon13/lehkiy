import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, Text
from aiogram.types import Message, CallbackQuery, ContentType
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from config import BOT_TOKEN, ADMIN_ID
import asyncio
import json
from db import create_pool, init_db, add_client, add_sale, get_sales_report
from keyboards import get_start_keyboard, get_confirm_sale_keyboard
from utils import format_sale_items, format_report
from datetime import datetime

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временное хранилище продаж в памяти (для демонстрации)
user_sales = {}

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = get_start_keyboard()
    await message.answer("Salom! Iltimos, ro'yxatdan o'ting:", reply_markup=kb)

@dp.message(F.contact)
async def register_contact(message: Message):
    contact = message.contact
    full_name = message.from_user.full_name
    await add_client(contact.user_id or message.from_user.id, full_name, contact.phone_number)
    await message.answer("Ro'yxatdan muvaffaqiyatli o'tdingiz! Endi savdoni boshlashingiz mumkin.")
    user_sales[message.from_user.id] = {"items": {}, "total": 0}

@dp.message(Text("Savdo boshlash"))
async def start_sale(message: Message):
    user_sales[message.from_user.id] = {"items": {}, "total": 0}
    await message.answer("Mahsulot qo'shish uchun quyidagicha yozing:\nMahsulot nomi, miqdori, narxi.\nMasalan:\nOlma, 3, 5000")

@dp.message(F.text)
async def add_item(message: Message):
    user_id = message.from_user.id
    if user_id not in user_sales:
        await message.answer("Iltimos, avval ro'yxatdan o'ting.")
        return

    try:
        product, qty, price = [x.strip() for x in message.text.split(",")]
        qty = int(qty)
        price = float(price)
    except Exception:
        await message.answer("Noto'g'ri format. Iltimos: Mahsulot, miqdor, narx")
        return

    if product in user_sales[user_id]["items"]:
        user_sales[user_id]["items"][product]["quantity"] += qty
    else:
        user_sales[user_id]["items"][product] = {"quantity": qty, "price": price}

    user_sales[user_id]["total"] = sum(
        item["quantity"] * item["price"] for item in user_sales[user_id]["items"].values()
    )
    await message.answer(f"Qo'shildi! Jami summa: {user_sales[user_id]['total']} so'm")

    # Предложить отправить чек
    kb = get_confirm_sale_keyboard()
    await message.answer("Savdoni yakunlash va chek jo'natish uchun quyidagi tugmani bosing:", reply_markup=kb)

@dp.callback_query(Text("confirm_sale"))
async def confirm_sale(call: CallbackQuery):
    user_id = call.from_user.id
    if user_id not in user_sales or not user_sales[user_id]["items"]:
        await call.message.answer("Savdo topilmadi yoki bo'sh.")
        return

    await call.message.answer("Iltimos, chek fotosuratini yuboring.")

    # Ожидаем фото в следующем сообщении (упрощенно)
    dp.message.register(receive_receipt_photo, F.photo, state=None)

    await call.answer()

async def receive_receipt_photo(message: Message):
    user_id = message.from_user.id
    if user_id not in user_sales:
        await message.answer("Savdo topilmadi.")
        return

    photo = message.photo[-1]
    file_id = photo.file_id

    # Сохраняем чек в базе
    await add_sale(
        client_id=user_id,
        items=user_sales[user_id]["items"],
        total_amount=user_sales[user_id]["total"],
        receipt_photo=file_id
    )

    # Формируем текст чека
    receipt_text = "Sizning chek:\n\n"
    receipt_text += format_sale_items(user_sales[user_id]["items"])
    receipt_text += f"\nJami: {user_sales[user_id]['total']} so'm\n"
    receipt_text += f"Sana va vaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    # Отправляем чек клиенту
    await message.answer(receipt_text)
    await message.answer_photo(file_id, caption="Sizning chek rasmingiz")

    # Отправляем админу
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID, f"Yangi savdo:\n\n{receipt_text}")
        await bot.send_photo(ADMIN_ID, file_id, caption="Savdo chеki")

    # Очистить временные данные
    user_sales.pop(user_id, None)

async def on_startup():
    await create_pool()
    await init_db()
    logging.info("Bot started!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(on_startup())
    from aiogram import executor
    executor.start_polling(dp)
