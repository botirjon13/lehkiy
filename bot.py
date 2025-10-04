import os
import asyncio
from decimal import Decimal
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, Text
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import asyncpg

TOKEN = os.getenv("TELEGRAM_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMINS = [123456789]  # Telegram user_id lar

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool: asyncpg.pool.Pool | None = None

# --- CallbackData ---
class BuyCallback(CallbackData, prefix="buy"):
    product_id: int

# --- Helpers ---
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

# --- DB init ---
async def init_db_pool():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                quantity INT,
                price NUMERIC(12,2)
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                product_id INT REFERENCES products(id),
                quantity INT,
                price NUMERIC(12,2),
                total NUMERIC(14,2),
                sale_date TIMESTAMP,
                seller_id BIGINT
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS carts (
                user_id BIGINT,
                product_id INT,
                quantity INT,
                PRIMARY KEY (user_id, product_id)
            );
        """)

# --- Sotish handler ---
@dp.message(Text("🛒 Sotish"))
async def start_sell(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Sizda ruxsat yo'q.")
        return
    await message.answer("Mahsulot nomini yozing (qidirish):")

# --- Qidiruv ---
@dp.message()
async def search_product(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    q = message.text.lower()
    rows = await db_pool.fetch(
        "SELECT id, name, quantity, price FROM products WHERE LOWER(name) LIKE $1 LIMIT 10",
        f"%{q}%"
    )
    if not rows:
        await message.answer("Mahsulot topilmadi.")
        return

    for r in rows:
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(
                text=f"🛒 Savatchaga qo‘shish ({r['price']})",
                callback_data=BuyCallback(product_id=r['id']).pack()
            )]
        ])
        await message.answer(f"{r['name']} — {r['quantity']} ta, narxi: {r['price']}", reply_markup=kb)

# --- Callback: savatchaga qo‘shish ---
@dp.callback_query(BuyCallback.filter())
async def add_to_cart(call: types.CallbackQuery, callback_data: BuyCallback):
    user_id = call.from_user.id
    product = await db_pool.fetchrow("SELECT id, name, quantity, price FROM products WHERE id=$1", callback_data.product_id)
    if not product:
        await call.answer("Mahsulot topilmadi", show_alert=True)
        return
    if product['quantity'] <= 0:
        await call.answer("Omborda mahsulot yo‘q", show_alert=True)
        return

    # Savatchaga qo‘shish yoki quantity oshirish
    await db_pool.execute("""
        INSERT INTO carts(user_id, product_id, quantity)
        VALUES($1, $2, 1)
        ON CONFLICT(user_id, product_id) DO UPDATE
        SET quantity = carts.quantity + 1
    """, user_id, product['id'])

    await call.answer(f"{product['name']} savatchaga qo‘shildi ✅")

# --- Savatchani ko‘rish ---
@dp.message(Text("🛒 Savatcha"))
async def view_cart(message: types.Message):
    user_id = message.from_user.id
    rows = await db_pool.fetch("""
        SELECT p.name, p.price, c.quantity
        FROM carts c
        JOIN products p ON p.id = c.product_id
        WHERE c.user_id=$1
    """, user_id)

    if not rows:
        await message.answer("Savatcha bo‘sh.")
        return

    total = sum(r['price'] * r['quantity'] for r in rows)
    text = "🛒 Sizning savatchangiz:\n"
    for r in rows:
        text += f"{r['name']} x {r['quantity']} — {r['price'] * r['quantity']}\n"
    text += f"\nJami: {total}"
    await message.answer(text)

# --- Savatchani sotish ---
@dp.message(Text("💳 Xarid qilish"))
async def checkout_cart(message: types.Message):
    user_id = message.from_user.id
    rows = await db_pool.fetch("""
        SELECT p.id, p.name, p.price, c.quantity, p.quantity AS stock
        FROM carts c
        JOIN products p ON p.id = c.product_id
        WHERE c.user_id=$1
    """, user_id)

    if not rows:
        await message.answer("Savatcha bo‘sh.")
        return

    for r in rows:
        if r['quantity'] > r['stock']:
            await message.answer(f"{r['name']} omborda yetarli emas!")
            return

    for r in rows:
        total_price = r['price'] * r['quantity']
        await db_pool.execute("""
            INSERT INTO sales(product_id, quantity, price, total, sale_date, seller_id)
            VALUES($1,$2,$3,$4,$5,$6)
        """, r['id'], r['quantity'], r['price'], total_price, datetime.utcnow(), user_id)
        await db_pool.execute("UPDATE products SET quantity=quantity-$1 WHERE id=$2", r['quantity'], r['id'])

    # Savatchani bo‘shatish
    await db_pool.execute("DELETE FROM carts WHERE user_id=$1", user_id)
    await message.answer("✅ Xarid muvaffaqiyatli amalga oshirildi!")

# --- Run ---
async def main():
    await init_db_pool()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
