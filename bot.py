#!/usr/bin/env python3
# bot.py
# To'liq yangilangan: mahsulot qo'shish, qidiruv tugmachali sotish, statistika (kun/oy/yil) + grafik

import os
import io
import asyncio
import hashlib
import logging
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv

import asyncpg
import matplotlib
matplotlib.use('Agg')  # serverda grafik chizish uchun
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError("TELEGRAM_TOKEN va DATABASE_URL o'rnatilishi shart")

# --- Bot & Dispatcher ---
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB pool ---
db_pool: asyncpg.pool.Pool | None = None

async def init_db_pool():
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                quantity INT NOT NULL,
                price NUMERIC(12,2) NOT NULL,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                product_id INT REFERENCES products(id),
                quantity INT NOT NULL,
                price NUMERIC(12,2) NOT NULL,
                total NUMERIC(14,2) NOT NULL,
                client_name TEXT,
                client_phone TEXT,
                payment_method TEXT,
                sale_date TIMESTAMP NOT NULL,
                seller_id BIGINT
            );
            """
        )
    return pool

# --- Admin tekshiruvi ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

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

# --- Klaviaturalar ---
def main_menu_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="➕ Mahsulot qo‘shish")],
        [KeyboardButton(text="🛒 Sotish")],
        [KeyboardButton(text="📊 Statistika")],
    ], resize_keyboard=True)
    return kb


def payment_kb():
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💵 Naqd"), KeyboardButton(text="💳 Karta")],
        [KeyboardButton(text="📅 Qarzga")],
    ], resize_keyboard=True, one_time_keyboard=True)
    return kb

# --- /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda bu botdan foydalanish huquqi yo‘q.")
        return

    await state.clear()
    text = (
        "Salom! Men sizning CRM botingizman.\n\n"
        "Mavjud buyruqlar:\n"
        "➕ Mahsulot qo‘shish — yangi mahsulot qo‘shish yoki mavjudini yangilash\n"
        "🛒 Sotish — mahsulot sotish (qidiruv tugmachali)\n"
        "📊 Statistika — kun/oy/yil bo‘yicha hisobot va grafik\n\n"
        "Misol mahsulot qo‘shish uchun: `Olma, 10, 15000`\n\n"
        "Tez qidiruv uchun chatda yozing:\n"
        "`@SizningBotingiz <mahsol_nomi>`"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="Markdown")

# --- Mahsulot qo'shish ---
@dp.message(Text("➕ Mahsulot qo‘shish"))
async def start_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda ruxsat yo‘q.")
        return
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer("Mahsulotni shu formatda yuboring: nomi, miqdori, narxi\n\nMisol: Olma, 10, 15000")


@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
    text = message.text.strip()
    parts = [p.strip() for p in text.split(",")]
    try:
        if len(parts) == 3:
            name = parts[0]
            qty = int(parts[1])
            price = Decimal(parts[2].replace(" ", ""))
        else:
            await message.answer("Format noto‘g‘ri. Misol: Olma, 10, 15000")
            return
    except Exception as e:
        logger.exception("Add product parse error")
        await message.answer("Xatolik! Iltimos formatni tekshiring va qayta yuboring.")
        return

    try:
        async with db_pool.acquire() as conn:
            product = await conn.fetchrow("SELECT id FROM products WHERE name=$1", name)
            if product:
                await conn.execute(
                    "UPDATE products SET quantity=quantity+$1, price=$2, updated_at=now() WHERE id=$3",
                    qty, price, product['id']
                )
                await message.answer(f"✅ Mahsulot yangilandi: {name}, +{qty} dona, narx: {price}")
            else:
                await conn.execute(
                    "INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)",
                    name, qty, price
                )
                await message.answer(f"✅ Yangi mahsulot qo‘shildi: {name}, miqdor: {qty}, narx: {price}")
    except Exception:
        logger.exception("DB error on add product")
        await message.answer("Bazaga yozishda xatolik yuz berdi. Later qayta urinib ko‘ring.")
    finally:
        await state.clear()

# --- Sotish (qidiruv tugmachali) ---
@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda ruxsat yo‘q.")
        return
    await state.set_state(SellStates.waiting_for_product)
    # Qo'llanma matni — foydalanuvchi mahsulot nomini yozadi
    await message.answer(
        "🔎 Qaysi mahsulotni sotmoqchisiz?\n"
        "Nomini yozing (masalan: Olma).\n"
        "Agar aniq nomni bilsangiz, barchani emas, faqat qismini yozing (masalan: olma)."
    )


@dp.message(SellStates.waiting_for_product)
async def search_product(message: types.Message, state: FSMContext):
    query = message.text.strip().lower()
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, price, quantity FROM products WHERE LOWER(name) LIKE $1 ORDER BY name LIMIT 10",
                f"%{query}%"
            )
    except Exception:
        logger.exception("DB error on search")
        await message.answer("Bazadan qidirishda xatolik yuz berdi. Qayta urinib ko‘ring.")
        return

    if not rows:
        await message.answer("❌ Mahsulot topilmadi. Boshqa nom bilan urinib ko‘ring.")
        return

    kb = InlineKeyboardBuilder()
    for product in rows:
        # Tugma matni: nom (miqdor dona, narx)
        txt = f"{product['name']} — {product['quantity']} dona, {product['price']}"
        kb.button(text=txt, callback_data=f"choose_product:{product['id']}")
    kb.adjust(1)

    await message.answer("🔽 Topilgan mahsulotlardan birini tanlang:", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data and c.data.startswith("choose_product"))
async def choose_product(callback: types.CallbackQuery, state: FSMContext):
    try:
        product_id = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("Noto'g'ri tugma.", show_alert=True)
        return

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, name, price, quantity FROM products WHERE id=$1", product_id)
    except Exception:
        logger.exception("DB error on fetch product by id")
        await callback.answer("Xatolik. Keyinroq urinib ko‘ring.", show_alert=True)
        return

    if not row:
        await callback.answer("Mahsulot topilmadi.", show_alert=True)
        return

    # State ga minimal ma'lumot saqlaymiz (asyncpg.Record obyekti saqlash xavfsiz emas)
    product = {
        'id': row['id'],
        'name': row['name'],
        'price': str(row['price']),
        'quantity': row['quantity']
    }

    await state.update_data(product=product)
    await state.set_state(SellStates.waiting_for_quantity)

    await callback.message.answer(
        f"✅ Tanlandi: {product['name']}\n"
        f"Narxi: {product['price']}\n"
        f"Omborda: {product['quantity']} dona\n\n"
        f"✍️ Necha dona sotamiz?"
    )
    await callback.answer()


@dp.message(SellStates.waiting_for_quantity)
async def process_sell_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    product = data.get('product')
    if not product:
        await message.answer("Xatolik: mahsulot tanlanmagan. Yana boshlang.")
        await state.clear()
        return

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ Miqdorni butun son sifatida kiriting.")
        return

    if qty > product['quantity']:
        await message.answer(f"⚠️ Omborda faqat {product['quantity']} dona mavjud.")
        return

    await state.update_data(quantity=qty)
    await state.set_state(SellStates.waiting_for_client_name)
    await message.answer("👤 Mijoz ismini kiriting (agar kerak bo‘lmasa '—' deb yozing):")


@dp.message(SellStates.waiting_for_client_name)
async def process_client_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if name == '':
        await message.answer("Mijoz ismi bo‘sh bo‘lishi mumkin emas. Agar yo‘q bo‘lsa '-' deb yozing.")
        return
    await state.update_data(client_name=name)
    await state.set_state(SellStates.waiting_for_client_phone)
    await message.answer("📞 Mijoz telefon raqamini kiriting (agar yo‘q bo‘lsa '-'):")


@dp.message(SellStates.waiting_for_client_phone)
async def process_client_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if phone == '':
        await message.answer("Iltimos telefon maydonini to‘ldiring yoki '-' deb yozing.")
        return
    await state.update_data(client_phone=phone)
    await state.set_state(SellStates.waiting_for_payment)
    await message.answer("💳 To‘lov turini tanlang:", reply_markup=payment_kb())


@dp.message(SellStates.waiting_for_payment)
async def process_payment(message: types.Message, state: FSMContext):
    pay_method = message.text.strip()
    if pay_method not in ["💵 Naqd", "💳 Karta", "📅 Qarzga"]:
        await message.answer("❌ Iltimos to‘lov turini klaviaturadan tanlang.")
        return
    await state.update_data(payment_method=pay_method)

    data = await state.get_data()
    product = data['product']
    qty = data['quantity']
    price = Decimal(product['price'])
    total = price * qty

    await message.answer(
        f"📋 Sotuv ma'lumotlari:\n\n"
        f"🛒 Mahsulot: {product['name']}\n"
        f"📦 Miqdor: {qty}\n"
        f"💵 Narx (1 dona): {price}\n"
        f"💰 Umumiy: {total}\n"
        f"👤 Mijoz: {data['client_name']}\n"
        f"📞 Telefon: {data['client_phone']}\n"
        f"💳 To‘lov: {pay_method}\n\n"
        f"✅ Tasdiqlash uchun 'ha' yozing yoki bekor qilish uchun 'bekor' yozing."
    )
    await state.set_state(SellStates.confirm)


@dp.message(SellStates.confirm)
async def process_confirm(message: types.Message, state: FSMContext):
    text = message.text.strip().lower()
    if text == "ha":
        data = await state.get_data()
        product = data['product']
        qty = data['quantity']
        client_name = data['client_name']
        client_phone = data['client_phone']
        payment_method = data['payment_method']
        price = Decimal(product['price'])
        total = price * qty

        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO sales(product_id, quantity, price, total, client_name, client_phone, payment_method, sale_date, seller_id) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    product['id'], qty, price, total, client_name, client_phone, payment_method, datetime.utcnow(), message.from_user.id
                )
                await conn.execute(
                    "UPDATE products SET quantity=quantity-$1, updated_at=now() WHERE id=$2",
                    qty, product['id']
                )
        except Exception:
            logger.exception("DB error on insert sale")
            await message.answer("Xatolik yuz berdi. Savdoni saqlash muvaffaqiyatsiz bo‘ldi.")
            await state.clear()
            return

        await message.answer("✅ Sotuv muvaffaqiyatli amalga oshirildi!", reply_markup=main_menu_kb())
        await state.clear()

    elif text == "bekor":
        await message.answer("❌ Sotuv bekor qilindi.", reply_markup=main_menu_kb())
        await state.clear()
    else:
        await message.answer("Iltimos 'ha' yoki 'bekor' deb yozing.")

# --- Inline qidiruv (har qanday chatda @bot nomi bilan) ---
@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    query = inline_query.query.strip().lower()
    if not query:
        await inline_query.answer(results=[], cache_time=1)
        return

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, price, quantity FROM products WHERE LOWER(name) LIKE $1 ORDER BY name LIMIT 10",
                f"%{query}%"
            )
    except Exception:
        logger.exception("DB error on inline search")
        await inline_query.answer(results=[], cache_time=1)
        return

    results = []
    for product in rows:
        msg_text = (
            f"Mahsulot: {product['name']}\n"
            f"Narx: {product['price']}\n"
            f"Omborda: {product['quantity']} dona"
        )
        result_id = hashlib.md5(f"{product['id']}".encode()).hexdigest()
        # Fix small typing issue above

    # Note: building results in a safe way
    results = []
    for product in rows:
        msg_text = (
            f"Mahsulot: {product['name']}\n"
            f"Narx: {product['price']}\n"
            f"Omborda: {product['quantity']} dona"
        )
        result_id = hashlib.md5(str(product['id']).encode()).hexdigest()
        results.append(
            InlineQueryResultArticle(
                id=result_id,
                title=product['name'],
                input_message_content=InputTextMessageContent(message_text=msg_text),
                description=f"Narx: {product['price']}, Omborda: {product['quantity']}"
            )
        )

    await inline_query.answer(results=results, cache_time=10, is_personal=True)

# --- Statistika (matn + grafik) ---
@dp.message(Text("📊 Statistika"))
async def show_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda ruxsat yo‘q.")
        return

    try:
        async with db_pool.acquire() as conn:
            total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales")
            my_total = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE seller_id=$1", message.from_user.id)

            today = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date)=CURRENT_DATE")
            my_today = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date)=CURRENT_DATE AND seller_id=$1", message.from_user.id)

            month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date)=DATE_TRUNC('month', CURRENT_DATE)")
            my_month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date)=DATE_TRUNC('month', CURRENT_DATE) AND seller_id=$1", message.from_user.id)

            year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date)=DATE_TRUNC('year', CURRENT_DATE)")
            my_year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date)=DATE_TRUNC('year', CURRENT_DATE) AND seller_id=$1", message.from_user.id)
    except Exception:
        logger.exception("DB error on stats")
        await message.answer("Statistikani olishda xatolik yuz berdi.")
        return

    text = (
        "📊 *Savdo statistikasi*\n\n"
        f"💰 Umumiy daromad: {total}\n"
        f"👤 Sizning umumiy savdolaringiz: {my_total}\n\n"
        f"📅 Bugungi savdolar: {today}\n"
        f"👤 Sizning bugungi savdolaringiz: {my_today}\n\n"
        f"🗓 Oylik savdolar: {month}\n"
        f"👤 Sizning oylik savdolaringiz: {my_month}\n\n"
        f"📆 Yillik savdolar: {year}\n"
        f"👤 Sizning yillik savdolaringiz: {my_year}\n"
    )

    await message.answer(text, parse_mode="Markdown")

    # Grafik
    try:
        labels = ["Bugungi", "Oylik", "Yillik"]
        values = [float(today), float(month), float(year)]

        plt.figure(figsize=(6, 4))
        bars = plt.bar(labels, values)
        plt.title("📊 Savdo Statistikasi")
        plt.xlabel("Davr")
        plt.ylabel("Summa")
        for bar in bars:
            h = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2, h, f"{h:.2f}", ha='center', va='bottom')

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()

        photo = FSInputFile(buf, filename="stats.png")
        await message.answer_photo(photo, caption="📊 Grafik ko‘rinishda")
    except Exception:
        logger.exception("Error creating chart")
        # grafik xatosi bo'lsa ham matnli statistikani yubordik
        return

# --- Start polling ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    logger.info("DB pool tayyor, bot ishga tushmoqda...")
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
