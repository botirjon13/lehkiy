import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL")

async def connect_db():
    return await asyncpg.connect(DATABASE_URL)

async def create_tables(conn):
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id BIGINT PRIMARY KEY,
        username TEXT,
        phone TEXT
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        price NUMERIC NOT NULL
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id SERIAL PRIMARY KEY,
        client_id BIGINT REFERENCES clients(id),
        total_amount NUMERIC NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS sales_products (
        sale_id INTEGER REFERENCES sales(id),
        product_id INTEGER REFERENCES products(id),
        quantity INTEGER DEFAULT 1,
        PRIMARY KEY (sale_id, product_id)
    );
    """)

    await conn.execute("""
    CREATE TABLE IF NOT EXISTS checks (
        id SERIAL PRIMARY KEY,
        sale_id INTEGER REFERENCES sales(id),
        file_path TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
