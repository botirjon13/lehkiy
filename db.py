import asyncpg
from config import DATABASE_URL

pool = None

async def create_pool():
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    async with pool.acquire() as conn:
        # Создаем таблицу clients
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            full_name TEXT,
            phone TEXT,
            registered_at TIMESTAMP DEFAULT now()
        );
        """)

        # Создаем таблицу sales
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            client_id BIGINT REFERENCES clients(telegram_id) ON DELETE CASCADE,
            items JSONB,
            total_amount NUMERIC,
            receipt_photo TEXT,
            sale_time TIMESTAMP DEFAULT now()
        );
        """)

async def add_client(telegram_id: int, full_name: str, phone: str):
    async with pool.acquire() as conn:
        # Добавляем клиента, если telegram_id уже есть, ничего не делаем
        await conn.execute("""
        INSERT INTO clients(telegram_id, full_name, phone) VALUES ($1, $2, $3)
        ON CONFLICT (telegram_id) DO NOTHING;
        """, telegram_id, full_name, phone)

async def add_sale(client_id: int, items: dict, total_amount: float, receipt_photo: str):
    async with pool.acquire() as conn:
        # Вставляем новую продажу
        await conn.execute("""
        INSERT INTO sales(client_id, items, total_amount, receipt_photo)
        VALUES ($1, $2, $3, $4)
        """, client_id, items, total_amount, receipt_photo)

async def get_sales_report(start_date, end_date):
    async with pool.acquire() as conn:
        # Получаем отчет по продажам за период
        records = await conn.fetch("""
        SELECT sale_time, total_amount FROM sales
        WHERE sale_time BETWEEN $1 AND $2
        ORDER BY sale_time ASC
        """, start_date, end_date)
        return records
