import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

async def connect_db():
    return await asyncpg.connect(DATABASE_URL)

async def create_tables(conn):
    # Таблица клиентов
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id BIGINT PRIMARY KEY,
            username TEXT
        );
    """)
    # Таблица товаров
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price NUMERIC(10,2) NOT NULL
        );
    """)
    # Таблица продаж
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            client_id BIGINT REFERENCES clients(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Таблица связей продаж–товары
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS sales_products (
            sale_id INT REFERENCES sales(id),
            product_id INT REFERENCES products(id),
            PRIMARY KEY (sale_id, product_id)
        );
    """)
