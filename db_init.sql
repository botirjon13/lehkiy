-- =========================
-- 0) Extensions (optional)
-- =========================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================
-- 1) Base tables (MUST exist before FK tables)
-- =========================

-- Customers
CREATE TABLE IF NOT EXISTS customers (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- Products
CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price BIGINT NOT NULL DEFAULT 0,
  stock INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'UZS',
  created_at TIMESTAMP DEFAULT now()
);

-- Add usd_rate column (you requested)
ALTER TABLE products
ADD COLUMN IF NOT EXISTS usd_rate NUMERIC(12,2);

-- Sales
CREATE TABLE IF NOT EXISTS sales (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
  total BIGINT NOT NULL DEFAULT 0,
  paid BIGINT NOT NULL DEFAULT 0,
  debt BIGINT NOT NULL DEFAULT 0,
  comment TEXT,
  created_at TIMESTAMP DEFAULT now()
);

-- =========================
-- 2) Dependent tables (your tables)
-- =========================

-- Sale items
CREATE TABLE IF NOT EXISTS sale_items (
  id SERIAL PRIMARY KEY,
  sale_id INTEGER REFERENCES sales(id) ON DELETE CASCADE,
  product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
  name TEXT,
  qty INTEGER NOT NULL DEFAULT 0,
  price BIGINT NOT NULL DEFAULT 0, -- price per item at sale time
  total BIGINT NOT NULL DEFAULT 0
);

-- Debts
CREATE TABLE IF NOT EXISTS debts (
  id SERIAL PRIMARY KEY,
  customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
  sale_id INTEGER REFERENCES sales(id) ON DELETE CASCADE,
  amount BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT now()
);

-- User carts
CREATE TABLE IF NOT EXISTS user_carts (
  user_id BIGINT PRIMARY KEY,
  data JSONB NOT NULL DEFAULT '{}'::jsonb, -- {items: [...], customer_id:..., temp:...}
  updated_at TIMESTAMP DEFAULT now()
);

-- Web users
CREATE TABLE IF NOT EXISTS web_users (
  id SERIAL PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'seller',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMP DEFAULT now()
);

-- =========================
-- 3) Helpful indexes (optional but recommended)
-- =========================
CREATE INDEX IF NOT EXISTS idx_sales_user_id ON sales(user_id);
CREATE INDEX IF NOT EXISTS idx_sales_customer_id ON sales(customer_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale_id ON sale_items(sale_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_product_id ON sale_items(product_id);
CREATE INDEX IF NOT EXISTS idx_debts_customer_id ON debts(customer_id);
CREATE INDEX IF NOT EXISTS idx_debts_sale_id ON debts(sale_id);

-- =========================
-- 4) Trigger to auto-update user_carts.updated_at
-- =========================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_carts_updated_at ON user_carts;
CREATE TRIGGER trg_user_carts_updated_at
BEFORE UPDATE ON user_carts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
