#!/usr/bin/env python3
# bot.py
# To'liq yangilangan — o'zbekcha, qidiruv tugmachali sotish, statistikalar (kun/oy/yil)

import os
import io
import asyncio
import logging
from decimal import Decimal
from datetime import datetime
from dotenv import load_dotenv
import hashlib

import asyncpg
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, Text
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    KeyboardButton, ReplyKeyboardMarkup,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    CallbackQuery, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN') or os.getenv('BOT_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
ADMINS = [int(x.strip()) for x in os.getenv('ADMINS', '').split(',') if x.strip()]

if not TELEGRAM_TOKEN or not DATABASE_URL:
    raise RuntimeError('TELEGRAM_TOKEN va DATABASE_URL muhit o\'zgaruvchilari sozlanmagan')

# --- Bot & Dispatcher ---
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- DB pool ---
db_pool: asyncpg.pool.Pool | None = None

async def init_db_pool():
    # asyncpg will parse sslmode if provided in DATABASE_URL
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        async with pool.acquire() as conn:
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                quantity INT NOT NULL,
                price NUMERIC(12,2) NOT NULL,
                created_at TIMESTAMP DEFAULT now(),
                updated_at TIMESTAMP DEFAULT now()
            );
            ''')
            await conn.execute('''
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
            ''')
        logger.info('DB pool tayyor')
        return pool
    except Exception:
        logger.exception('DB pool yaratishda xato')
        raise

# --- Helpers ---

def is_admin(user_id: int) -> bool:
    # Agar ADMINS bo'sh bo'lsa hamma ruxsatli bo'lmasin
    if not ADMINS:
        return False
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

# --- Keyboards ---

def main_menu_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton('➕ Mahsulot qo\'shish')],
        [KeyboardButton('🛒 Sotish')],
        [KeyboardButton('📊 Hisobot')],
    ], resize_keyboard=True)


def payment_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton('💵 Naqd'), KeyboardButton('💳 Karta')],
        [KeyboardButton('📅 Qarzga')],
    ], resize_keyboard=True, one_time_keyboard=True)

# --- Handlers ---
@dp.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer('⛔ Sizda bu botdan foydalanish huquqi yo\'q.')
        return
    await state.clear()
    text = (
        'Salom! CRM botga hush kelibsiz.\n\n'
        'Buyruqlar:\n'
        '➕ Mahsulot qo\'shish — yangi mahsulot qo\'shish yoki yangilash\n'
        '🛒 Sotish — mahsulotni qidirib sotish (qulay qidiruv tugmasi mavjud)\n'
        '📊 Hisobot — kun/oy/yil bo\'yicha hisobot va grafik\n\n'
        'Misol: mahsulot qo\'shish uchun `Olma, 10, 5000`\n'
        'Sotish uchun: "🛒 Sotish" tugmasini bosing va pastdagi "🔍 Mahsulot qidirish" tugmasini ishlating.'
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode='Markdown')

# --- Add product ---
@dp.message(Text("➕ Mahsulot qo'\shish"))
async def start_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer('⛔ Sizda ruxsat yo\'q.')
        return
    await state.set_state(AddProductStates.waiting_for_input)
    await message.answer('Mahsulotni yuboring: nomi, miqdori, narxi\nMisol: Olma, 10, 5000')

@dp.message(AddProductStates.waiting_for_input)
async def process_add_input(message: types.Message, state: FSMContext):
    text = message.text.strip()
    parts = [p.strip() for p in text.split(',')]
    try:
        if len(parts) == 3:
            name = parts[0]
            qty = int(parts[1])
            price = Decimal(parts[2].replace(' ', ''))
        else:
            await message.answer('Format noto\'g\'ri. Misol: Olma, 10, 5000')
            return
    except Exception:
        await message.answer('Xatolik: ma\'lumotlarni tekshiring.')
        return

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT id FROM products WHERE name=$1', name)
            if row:
                await conn.execute('UPDATE products SET quantity=quantity+$1, price=$2, updated_at=now() WHERE id=$3', qty, price, row['id'])
                await message.answer(f'✅ {name} yangilandi: +{qty}, narxi: {price}')
            else:
                await conn.execute('INSERT INTO products(name, quantity, price) VALUES($1,$2,$3)', name, qty, price)
                await message.answer(f'✅ Yangi mahsulot qo\'shildi: {name}, {qty} dona, {price}')
    except Exception:
        logger.exception('Add product DB error')
        await message.answer('Bazaga yozishda xatolik yuz berdi.')
    finally:
        await state.clear()

# --- Sotish: biz pastdagi switch tugmasi orqali inline rejimni chaqiramiz ---
@dp.message(Text('🛒 Sotish'))
async def start_sell(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer('⛔ Sizda ruxsat yo\'q.')
        return
    # switch_inline_query_current_chat="" ochadi va foydalanuvchi faqat qidiruv so'zini yozadi
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔍 Mahsulot qidirish', switch_inline_query_current_chat='')]
    ])
    await message.answer('Qidirayotgan mahsulotingiz nomining bosh harflarini yozing:', reply_markup=kb)

# --- Inline qidiruv handler ---
@dp.inline_query()
async def inline_search(inline_query: InlineQuery):
    q = inline_query.query.strip()
    if not q:
        return await inline_query.answer(results=[], cache_time=1)

    try:
        rows = await db_pool.fetch("SELECT id, name, quantity, price FROM products WHERE LOWER(name) LIKE $1 ORDER BY name LIMIT 10", f"%{q.lower()}%")
    except Exception:
        logger.exception('Inline search DB error')
        return await inline_query.answer(results=[], cache_time=1)

    results = []
    for r in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Sotib olish ({r['price']})", callback_data=f"buy:{r['id']}")]
        ])
        msg = f"{r['name']} — narxi: {r['price']} (omborda: {r['quantity']})"
        results.append(InlineQueryResultArticle(
            id=hashlib.md5(str(r['id']).encode()).hexdigest(),
            title=r['name'],
            description=f"{r['quantity']} dona | {r['price']}",
            input_message_content=InputTextMessageContent(message_text=msg),
            reply_markup=kb
        ))

    await inline_query.answer(results=results, cache_time=1, is_personal=True)

# --- Callback: buy ---
@dp.callback_query(F.data.startswith('buy:'))
async def handle_buy(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer('⛔ Sizda ruxsat yo\'q.', show_alert=True)

    try:
        product_id = int(call.data.split(':', 1)[1])
    except Exception:
        return await call.answer('Noto\'g\'ri ma\'lumot', show_alert=True)

    try:
        row = await db_pool.fetchrow('SELECT id, name, quantity, price FROM products WHERE id=$1', product_id)
    except Exception:
        logger.exception('DB error on fetch product')
        return await call.answer('Xatolik yuz berdi', show_alert=True)

    if not row:
        return await call.answer('Mahsulot topilmadi', show_alert=True)

    if row['quantity'] <= 0:
        return await call.answer('Omborda mahsulot qolmagan', show_alert=True)

    # Bu yerda biz oddiy oqim: 1 dona kamaytirish va savdoni yozish
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id) VALUES($1,$2,$3,$4,$5,$6)', row['id'], 1, row['price'], row['price'], datetime.utcnow(), call.from_user.id)
            await conn.execute('UPDATE products SET quantity = quantity - 1, updated_at=now() WHERE id=$1', row['id'])
    except Exception:
        logger.exception('DB error on buy')
        return await call.answer('Savdoni saqlashda xatolik', show_alert=True)

    # Yangilangan ombor sonini olish
    remaining = row['quantity'] - 1
    try:
        await call.message.edit_text(f"✅ {row['name']} sotildi!\nQolgan: {remaining} ta")
    except Exception:
        # Ba'zan edit_text ishlamasligi mumkin (masalan, xabar o'zgartirilsa) — shunda yangi xabar jo'natamiz
        await call.message.answer(f"✅ {row['name']} sotildi!\nQolgan: {remaining} ta")

    await call.answer('✅ Sotib olindi')

# --- Hisobot (kun/oy/yil) + grafik ---
@dp.message(Text('📊 Hisobot'))
async def stats_handler(message: types.Message):
    if not is_admin(message.from_user.id):
        return await message.answer('⛔ Sizda ruxsat yo\'q.')

    try:
        async with db_pool.acquire() as conn:
            total = await conn.fetchval('SELECT COALESCE(SUM(total),0) FROM sales')
            today = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE(sale_date)=CURRENT_DATE")
            month = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('month', sale_date)=DATE_TRUNC('month', CURRENT_DATE)")
            year = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM sales WHERE DATE_TRUNC('year', sale_date)=DATE_TRUNC('year', CURRENT_DATE)")
            my_total = await conn.fetchval('SELECT COALESCE(SUM(total),0) FROM sales WHERE seller_id=$1', message.from_user.id)
    except Exception:
        logger.exception('Stats DB error')
        return await message.answer('Statistikani olishda xatolik yuz berdi')

    text = (
        f"📊 Savdo statistikasi:\n\n"
        f"💰 Umumiy daromad: {total}\n"
        f"📅 Bugungi: {today}\n"
        f"🗓 Oylik: {month}\n"
        f"📆 Yillik: {year}\n\n"
        f"👤 Sizning umumiy savdolaringiz: {my_total}"
    )
    await message.answer(text)

    # Grafik
    try:
        labels = ['Bugungi', 'Oylik', 'Yillik']
        values = [float(today), float(month), float(year)]
        plt.figure(figsize=(6,4))
        bars = plt.bar(labels, values)
        for bar in bars:
            h = bar.get_height()
            plt.text(bar.get_x()+bar.get_width()/2, h, f"{h:.2f}", ha='center', va='bottom')
        plt.title('Savdo statistikasi')
        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        await message.answer_photo(photo=FSInputFile(buf, filename='stats.png'), caption='Grafik')
    except Exception:
        logger.exception('Chart error')

# --- Run ---
async def main():
    global db_pool
    db_pool = await init_db_pool()
    logger.info('Bot ishga tushmoqda...')
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi")
