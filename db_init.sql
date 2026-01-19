-- =====================================================
-- DB INIT FINAL (BOT + WEB, SAFE MIGRATION)
-- =====================================================

-- =========================
-- CUSTOMERS
-- =========================
CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS name TEXT;

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS phone TEXT;

ALTER TABLE customers
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();


-- =========================
-- PRODUCTS
-- =========================
CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  qty INTEGER NOT NULL DEFAULT 0,
  cost_price BIGINT NOT NULL DEFAULT 0,
  cost_price_usd NUMERIC(12,2) NOT NULL DEFAULT 0,
  usd_rate NUMERIC(12,2),
  suggest_price BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS name TEXT;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS qty INTEGER NOT NULL DEFAULT 0;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS cost_price BIGINT NOT NULL DEFAULT 0;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS cost_price_usd NUMERIC(12,2) NOT NULL DEFAULT 0;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS usd_rate NUMERIC(12,2);

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS suggest_price BIGINT NOT NULL DEFAULT 0;

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();


-- =========================
-- SALES
-- =========================
CREATE TABLE IF NOT EXISTS sales (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  total_amount BIGINT NOT NULL DEFAULT 0,
  payment_type TEXT NOT NULL DEFAULT 'naqd',
  seller_phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE sales
  ADD COLUMN IF NOT EXISTS customer_id INTEGER;

ALTER TABLE sales
  ADD COLUMN IF NOT EXISTS total_amount BIGINT NOT NULL DEFAULT 0;

ALTER TABLE sales
  ADD COLUMN IF NOT EXISTS payment_type TEXT NOT NULL DEFAULT 'naqd';

ALTER TABLE sales
  ADD COLUMN IF NOT EXISTS seller_phone TEXT;

ALTER TABLE sales
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();


-- =========================
-- SALE ITEMS
-- =========================
CREATE TABLE IF NOT EXISTS sale_items (
  id SERIAL PRIMARY KEY,
  sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  name TEXT,
  qty INTEGER NOT NULL DEFAULT 0,
  price BIGINT NOT NULL DEFAULT 0,
  total BIGINT NOT NULL DEFAULT 0
);

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS sale_id INTEGER;

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS product_id INTEGER;

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS name TEXT;

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS qty INTEGER NOT NULL DEFAULT 0;

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS price BIGINT NOT NULL DEFAULT 0;

ALTER TABLE sale_items
  ADD COLUMN IF NOT EXISTS total BIGINT NOT NULL DEFAULT 0;


-- =========================
-- DEBTS
-- =========================
CREATE TABLE IF NOT EXISTS debts (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
  sale_id INTEGER REFERENCES sales(id) ON DELETE CASCADE,
  amount BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE debts
  ADD COLUMN IF NOT EXISTS customer_id INTEGER;

ALTER TABLE debts
  ADD COLUMN IF NOT EXISTS sale_id INTEGER;

ALTER TABLE debts
  ADD COLUMN IF NOT EXISTS amount BIGINT NOT NULL DEFAULT 0;

ALTER TABLE debts
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();


-- =========================
-- USER CARTS
-- =========================
CREATE TABLE IF NOT EXISTS user_carts (
  user_id BIGINT PRIMARY KEY,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMP DEFAULT now()
);

ALTER TABLE user_carts
  ADD COLUMN IF NOT EXISTS data JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE user_carts
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now();


-- =========================
-- WEB USERS
-- =========================
CREATE TABLE IF NOT EXISTS web_users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'seller',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now()
);

ALTER TABLE web_users
  ADD COLUMN IF NOT EXISTS username TEXT;

ALTER TABLE web_users
  ADD COLUMN IF NOT EXISTS password_hash TEXT;

ALTER TABLE web_users
  ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'seller';

ALTER TABLE web_users
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE web_users
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT now();

-- =========================
-- FIX: sales.user_id nullable
-- =========================

ALTER TABLE sales
  ALTER COLUMN user_id DROP NOT NULL;



-- =========================
-- INDEXES (SAFE)
-- =========================
CREATE INDEX IF NOT EXISTS idx_products_name ON products (name);
CREATE INDEX IF NOT EXISTS idx_products_qty ON products (qty);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers (phone);

CREATE INDEX IF NOT EXISTS idx_sales_customer_id ON sales (customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_created_at ON sales (created_at);

CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id ON sale_items (sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product_id ON sale_items (product_id);

CREATE INDEX IF NOT EXISTS idx_debts_customer_id ON debts (customer_id);
CREATE INDEX IF NOT EXISTS idx_debts_sale_id ON debts (sale_id);
