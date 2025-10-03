import asyncpg
import os

DATABASE_URL = os.getenv("DATABASE_URL")

async def connect_db():
    return await asyncpg.connect(DATABASE_URL)

async def create_tables(conn):
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id BIGINT PRIMARY KEY,
            username TEXT
        );
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price NUMERIC NOT NULL
        );
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            client_id BIGINT REFERENCES clients(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS sales_products (
            sale_id INT REFERENCES sales(id),
            product_id INT REFERENCES products(id),
            PRIMARY KEY (sale_id, product_id)
        );
    ''')
