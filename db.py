# db.py
import asyncpg
from config import DATABASE_URL


async def connect_db():
    return await asyncpg.connect(DATABASE_URL)


async def create_tables():
    conn = await connect_db()

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id BIGINT PRIMARY KEY,
        full_name TEXT,
        phone TEXT
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        price NUMERIC(10, 2) NOT NULL
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        client_id BIGINT REFERENCES clients(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS sales_products (
        sale_id INT REFERENCES sales(id),
        product_id INT REFERENCES products(id),
        quantity INT NOT NULL,
        PRIMARY KEY (sale_id, product_id)
    );
    """)

    await conn.close()
